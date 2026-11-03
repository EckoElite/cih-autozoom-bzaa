package dev.cih.zoomspike

import android.app.Activity
import android.content.Intent
import android.content.pm.ActivityInfo
import android.graphics.Rect
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.util.Log
import android.view.Choreographer
import android.view.Gravity
import android.view.Surface
import android.view.SurfaceControl
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.TextView
import org.json.JSONObject
import java.io.File
import kotlin.math.floor
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Zoom frame-skip spike: plays a test clip with MediaCodec and applies the per-frame CIH zoom in one of four ways, so
 * the ways can be compared on the same hardware (see docs/ZOOM_SPIKE_REPORT.md).
 *
 *   N   no zoom control (baseline: only the pacing is measured)
 *   A   resize the SurfaceView through its layout params (what Kodi does)
 *   A2  as A, but the surface buffer is fixed to the video size, so a resize only scales in the compositor
 *   B   a SurfaceControl transaction on the SurfaceView's own layer (fixed-size surface, layout never changes)
 *   C   a SurfaceControl transaction on our own child layer of the SurfaceView
 *
 * Started from the PC:  am start -S -n dev.cih.zoomspike/.MainActivity --es mode C --es clip clip_4k_hdr10.mkv --ei lead 0
 * The clip and timeline_<clip>.json go in /sdcard/Android/data/dev.cih.zoomspike/files/. Results are written there
 * as run_<mode>_<clip>_lead<lead>_<time>.csv and summarised in logcat under the tag ZoomSpike.
 */
class MainActivity : Activity(), SurfaceHolder.Callback {
    private lateinit var surfaceView: SurfaceView
    private lateinit var root: FrameLayout
    private lateinit var label: TextView
    private var run: Run? = null
    private var started = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val clip = intent.getStringExtra("clip") ?: "clip_1080_sdr.mkv"
        if (clip.contains("hdr")) window.colorMode = ActivityInfo.COLOR_MODE_HDR
        root = FrameLayout(this)
        surfaceView = SurfaceView(this)
        label = TextView(this).apply {
            setTextColor(0xFFFFFF00.toInt())
            textSize = 18f
            setPadding(40, 0, 0, 0)
        }
        root.addView(surfaceView, FrameLayout.LayoutParams(-1, -1))
        root.addView(label, FrameLayout.LayoutParams(-2, -2, Gravity.TOP or Gravity.START))
        setContentView(root)
        val mode = intent.getStringExtra("mode") ?: "N"
        if (mode == "A2" || mode == "B") {
            val info = readTimeline(clip)
            surfaceView.holder.setFixedSize(info.width, info.height)
        }
        surfaceView.holder.addCallback(this)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // a new run needs a new process (a fresh surface): the PC starts each one with am start -S
        Log.w(TAG, "onNewIntent ignored; start runs with am start -S")
    }

    private fun readTimeline(clip: String): Timeline {
        val dir = getExternalFilesDir(null)!!
        val name = clip.removeSuffix(".mkv").removeSuffix(".mp4")
        return Timeline.load(File(dir, "timeline_$name.json"))
    }

    override fun surfaceCreated(holder: SurfaceHolder) {}

    override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
        if (started || width <= 0) return
        started = true
        val clip = intent.getStringExtra("clip") ?: "clip_1080_sdr.mkv"
        val mode = intent.getStringExtra("mode") ?: "N"
        val lead = intent.getIntExtra("lead", 0)
        val viewW = root.width
        val viewH = root.height
        val r = Run(this, surfaceView, label, clip, mode, lead, viewW, viewH, readTimeline(clip))
        run = r
        r.start()
    }

    override fun surfaceDestroyed(holder: SurfaceHolder) {}

    override fun onStop() {
        super.onStop()
        run?.stop()
        finish()
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        run?.stop()
        finish()
    }

    companion object {
        const val TAG = "ZoomSpike"
    }
}

class Timeline(val width: Int, val height: Int, val fps: Double, val rects: Array<IntArray>) {
    companion object {
        fun load(file: File): Timeline {
            val j = JSONObject(file.readText())
            val arr = j.getJSONArray("rects")
            val rects = Array(arr.length()) { i ->
                val a = arr.getJSONArray(i)
                intArrayOf(a.getInt(0), a.getInt(1), a.getInt(2), a.getInt(3))
            }
            return Timeline(j.getInt("width"), j.getInt("height"), j.getDouble("fps"), rects)
        }
    }
}

/** One playback of one clip in one zoom mode. */
class Run(
    private val activity: Activity,
    private val sv: SurfaceView,
    private val label: TextView,
    private val clip: String,
    private val mode: String,
    private val lead: Int,
    private val viewW: Int,
    private val viewH: Int,
    private val tl: Timeline,
) : Choreographer.FrameCallback {
    private val tag = MainActivity.TAG
    private val main = Handler(Looper.getMainLooper())
    private val log = ArrayList<String>(8192)
    @Volatile private var stopped = false
    @Volatile private var startNs = 0L
    private var lastApplied = -1
    private var thread: Thread? = null
    private var codec: MediaCodec? = null
    private var extractor: MediaExtractor? = null
    private var child: SurfaceControl? = null
    private var outSurface: Surface? = null
    private val tx = SurfaceControl.Transaction()
    private var vsyncNs = 16_666_667L
    private val framePeriodNs = (1e9 / tl.fps).toLong()

    // the band the picture must fit in, in view pixels (rows 263..1897 of 2160)
    private val bandTop = viewH * 263.0 / 2160.0
    private val bandH = viewH * 1634.0 / 2160.0

    private fun note(type: String, key: Long, tNs: Long, extra: String = "") {
        synchronized(log) { log.add("$type,$key,$tNs,$extra") }
    }

    fun start() {
        val display = activity.windowManager.defaultDisplay
        vsyncNs = (1e9 / display.refreshRate).toLong()
        val info = "mode=$mode clip=$clip lead=$lead view=${viewW}x${viewH} display=${display.refreshRate}Hz " +
            "mode=${display.mode.physicalWidth}x${display.mode.physicalHeight} video=${tl.width}x${tl.height}"
        Log.i(tag, "start $info")
        label.text = "$mode  $clip  lead $lead"
        label.setPadding(40, bandTop.toInt() + 10, 0, 0)
        note("info", 0, System.nanoTime(), info.replace(',', ' '))

        val file = File(activity.getExternalFilesDir(null), clip)
        val ex = MediaExtractor().also { extractor = it }
        ex.setDataSource(file.absolutePath)
        var track = -1
        for (i in 0 until ex.trackCount) {
            if (ex.getTrackFormat(i).getString(MediaFormat.KEY_MIME)!!.startsWith("video/")) { track = i; break }
        }
        require(track >= 0) { "no video track" }
        ex.selectTrack(track)
        val format = ex.getTrackFormat(track)
        Log.i(tag, "format $format")
        note("format", 0, System.nanoTime(), format.toString().replace(',', ' '))

        val surface = makeSurface()
        outSurface = surface
        try {
            surface.setFrameRate(tl.fps.toFloat(), Surface.FRAME_RATE_COMPATIBILITY_FIXED_SOURCE)
        } catch (e: Exception) {
            Log.w(tag, "setFrameRate failed: $e")
        }
        val c = MediaCodec.createDecoderByType(format.getString(MediaFormat.KEY_MIME)!!).also { codec = it }
        val cbThread = HandlerThread("rendered").also { it.start() }
        c.setOnFrameRenderedListener({ _, pts, nano -> note("rendered", pts, nano) }, Handler(cbThread.looper))
        c.configure(format, surface, null, 0)
        c.start()
        if (mode != "N") applyGeometry(0)
        Choreographer.getInstance().postFrameCallback(this)
        thread = Thread({ decodeLoop() }, "decode").also { it.start() }
    }

    /** The surface the codec draws on: the SurfaceView's own, or (mode C) one on our child layer. */
    private fun makeSurface(): Surface {
        if (mode != "C") return sv.holder.surface
        val sc = SurfaceControl.Builder()
            .setName("zoomspike-video")
            .setBufferSize(tl.width, tl.height)
            .setParent(sv.surfaceControl)
            .build()
        child = sc
        val d = destFor(0)
        tx.setVisibility(sc, true).setLayer(sc, 1)
            .setGeometry(sc, Rect(0, 0, tl.width, tl.height), d, Surface.ROTATION_0).apply()
        return Surface(sc)
    }

    /** Where the whole video frame must be drawn (in view pixels) so the active picture sits centred in the band. */
    private fun destFor(n: Int): Rect {
        val r = tl.rects[min(n, tl.rects.size - 1)]
        val aw = r[2].toDouble() / tl.width
        val ah = r[3].toDouble() / tl.height
        val z = min(1.0 / aw, bandH / (ah * viewH))
        val wd = viewW * z
        val hd = viewH * z
        val cx = (r[0] + r[2] / 2.0) / tl.width
        val cy = (r[1] + r[3] / 2.0) / tl.height
        val left = viewW / 2.0 - cx * wd
        val top = bandTop + bandH / 2.0 - cy * hd
        return Rect(left.roundToInt(), top.roundToInt(), (left + wd).roundToInt(), (top + hd).roundToInt())
    }

    private fun applyGeometry(n: Int) {
        val d = destFor(n)
        val t0 = System.nanoTime()
        when (mode) {
            "A", "A2" -> {
                val lp = FrameLayout.LayoutParams(d.width(), d.height())
                lp.leftMargin = d.left
                lp.topMargin = d.top
                sv.layoutParams = lp
            }
            "B" -> tx.setGeometry(sv.surfaceControl, Rect(0, 0, tl.width, tl.height), d, Surface.ROTATION_0).apply()
            "C" -> tx.setGeometry(child!!, Rect(0, 0, tl.width, tl.height), d, Surface.ROTATION_0).apply()
        }
        val t1 = System.nanoTime()
        note("zoom", n.toLong(), t0, "${d.left} ${d.top} ${d.width()} ${d.height()} call_us=${(t1 - t0) / 1000}")
    }

    /** At every vsync, make sure the geometry of the frame that will be on screen then is the one in place. */
    override fun doFrame(frameTimeNanos: Long) {
        if (stopped) return
        val s = startNs
        if (s != 0L && mode != "N") {
            val target = frameTimeNanos + (1 + lead) * vsyncNs
            val n = floor((target - s).toDouble() / framePeriodNs).toInt().coerceIn(0, tl.rects.size - 1)
            note("vsync", n.toLong(), frameTimeNanos)
            if (n != lastApplied && (lastApplied < 0 || !tl.rects[n].contentEquals(tl.rects[lastApplied]))) {
                applyGeometry(n)
            }
            lastApplied = n
        }
        Choreographer.getInstance().postFrameCallback(this)
    }

    private fun decodeLoop() {
        val c = codec!!
        val ex = extractor!!
        val bi = MediaCodec.BufferInfo()
        var inputDone = false
        var outputDone = false
        try {
            while (!outputDone && !stopped) {
                if (!inputDone) {
                    val i = c.dequeueInputBuffer(2000)
                    if (i >= 0) {
                        val n = ex.readSampleData(c.getInputBuffer(i)!!, 0)
                        if (n < 0) {
                            c.queueInputBuffer(i, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                            inputDone = true
                        } else {
                            c.queueInputBuffer(i, 0, n, ex.sampleTime, 0)
                            ex.advance()
                        }
                    }
                }
                val o = c.dequeueOutputBuffer(bi, 2000)
                if (o >= 0) {
                    if (startNs == 0L) startNs = System.nanoTime() + 500_000_000L
                    val target = startNs + bi.presentationTimeUs * 1000
                    while (System.nanoTime() < target - 35_000_000L && !stopped) Thread.sleep(1)
                    val frame = (bi.presentationTimeUs * tl.fps / 1e6).roundToInt()
                    note("release", frame.toLong(), System.nanoTime(), "target=$target")
                    c.releaseOutputBuffer(o, target)
                    if (bi.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) outputDone = true
                }
            }
            Thread.sleep(800) // let the last frames render
        } catch (e: Exception) {
            Log.e(tag, "decode loop failed", e)
            note("error", 0, System.nanoTime(), e.toString().replace(',', ' '))
        }
        main.post { finishRun() }
    }

    fun stop() {
        stopped = true
    }

    private fun finishRun() {
        stopped = true
        try { codec?.stop(); codec?.release() } catch (_: Exception) {}
        try { extractor?.release() } catch (_: Exception) {}
        try { outSurface?.release() } catch (_: Exception) {}
        child?.let { tx.reparent(it, null).apply(); it.release() }
        val stamp = System.currentTimeMillis() / 1000
        val name = "run_${mode}_${clip.substringBeforeLast('.')}_lead${lead}_$stamp.csv"
        val out = File(activity.getExternalFilesDir(null), name)
        synchronized(log) { out.writeText("type,key,t_ns,extra\n" + log.joinToString("\n") + "\n") }
        Log.i(tag, "wrote ${out.absolutePath} (${log.size} rows)")
        label.text = "done: $name"
        main.postDelayed({ activity.finish() }, 1500)
    }
}
