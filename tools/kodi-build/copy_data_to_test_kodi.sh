# Run on the Shield (adb push, then adb shell sh): copies the add-ons and settings the test Kodi needs from the Play Store Kodi.
# Needs the test app started once first (it creates its data folder). Afterwards: enable the add-ons over JSON-RPC.
S=/sdcard/Android/data/org.xbmc.kodi/files/.kodi
D=/sdcard/Android/data/org.xbmc.kodi.cih/files/.kodi
am force-stop org.xbmc.kodi.cih
sleep 2
for a in skin.scope.nox.omega script.plexmod service.cih.zoom script.skinshortcuts script.grab.fanart service.library.data.provider resource.images.studios.white resource.images.recordlabels.white; do
  rm -rf $D/addons/$a; cp -r $S/addons/$a $D/addons/$a && echo "addon $a ok"
done
for a in $(ls $S/addons | grep '^script.module.'); do rm -rf $D/addons/$a; cp -r $S/addons/$a $D/addons/$a; done
echo "modules: $(ls $D/addons | grep -c '^script.module.')"
for a in script.plexmod service.cih.zoom skin.scope.nox.omega script.skinshortcuts; do
  rm -rf $D/userdata/addon_data/$a; cp -r $S/userdata/addon_data/$a $D/userdata/addon_data/$a && echo "data $a ok"
done
cp $S/userdata/guisettings.xml $D/userdata/guisettings.xml
mkdir -p $D/userdata/keymaps; cp $S/userdata/keymaps/* $D/userdata/keymaps/
for f in advancedsettings.xml sources.xml profiles.xml favourites.xml; do [ -f $S/userdata/$f ] && cp $S/userdata/$f $D/userdata/$f && echo "userdata $f ok"; done
du -sk $D; df /data/user/0 | tail -1