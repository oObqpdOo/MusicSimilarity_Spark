#!/bin/sh

VPNINSTPREFIX="/opt/cisco/anyconnect"
BINDIR="${VPNINSTPREFIX}/bin"
LEGACY_DARTBIN="/opt/cisco/vpn/dart"
INSTPREFIX="${VPNINSTPREFIX}/dart"
CONFIGDIR="${INSTPREFIX}/xml/config"
REQUESTDIR="${INSTPREFIX}/xml/request"
PIXMAPSDIR="${INSTPREFIX}/pixmaps"
GLADEDIR="${INSTPREFIX}/glade"
MENUDIR="/etc/xdg/menus/applications-merged/"
DIRECTORYDIR="/usr/share/desktop-directories/"
DESKTOPDIR="/usr/share/applications"
LOGFILE="/tmp/dart-uninstall.log"
ACMANIFESTDAT="${VPNINSTPREFIX}/VPNManifest.dat"
DARTMANIFEST="ACManifestDART.xml"

# List of files to remove
FILELIST="${CONFIGDIR}/AnyConnectConfig.xml \
          ${CONFIGDIR}/BaseConfig.xml \
          ${CONFIGDIR}/Posture.xml \
          ${CONFIGDIR}/NetworkVisibility.xml \
          ${CONFIGDIR}/ConfigXMLSchema.xsd \
          ${REQUESTDIR}/RequestXMLSchema.xsd \
          ${PIXMAPSDIR}/ciscoLogo.png \
          ${PIXMAPSDIR}/dartCustom.png \
          ${PIXMAPSDIR}/dartTypical.png \
          ${GLADEDIR}/DARTGUI.glade \
          ${INSTPREFIX}/dartui \
          ${INSTPREFIX}/dartcli \
          ${BINDIR}/dart_uninstall.sh \
          ${MENUDIR}/cisco-anyconnect-dart.menu \
          ${DIRECTORYDIR}/cisco-anyconnect-dart.directory \
          ${DESKTOPDIR}/cisco-anyconnect-dart.desktop \
          ${VPNINSTPREFIX}/${DARTMANIFEST} \
          ${LEGACY_DARTBIN}/dart_uninstall.sh}"

echo "Uninstalling Cisco AnyConnect Diagnostics and Reporting Tool..."
echo "Uninstalling Cisco AnyConnect Diagnostics and Reporting Tool..." > ${LOGFILE}
echo `whoami` "invoked $0 from " `pwd` " at " `date` >> ${LOGFILE}

# Check for root privileges
if [ `id | sed -e 's/(.*//'` != "uid=0" ]; then
  echo "Sorry, you need super user privileges to run this script."
  echo "Sorry, you need super user privileges to run this script." >> ${LOGFILE}
  exit 1
fi

# make sure gui is not running
PROCS=`ps -A -o pid,command | grep '/opt/cisco/anyconnect/dart' | egrep -v 'grep|dart_uninstall' | awk '{print $1}'`
if [ -n "${PROCS}" ]; then 
    echo Killing `ps -A -o pid,command -p ${PROCS} | grep ${PROCS} | egrep -v 'ps|grep'` >> ${LOGFILE}
    kill -KILL ${PROCS} >> ${LOGFILE} 2>&1
fi

# update the VPNManifest.dat; if no entries remain in the .dat file then
# this tool will delete the file - DO NOT blindly delete VPNManifest.dat by
# adding it to the FILELIST above - allow this tool to delete the file if needed
if [ -f "${BINDIR}/manifesttool" ]; then
  echo "${BINDIR}/manifesttool -x ${VPNINSTPREFIX} ${VPNINSTPREFIX}/${DARTMANIFEST}" >> ${LOGFILE}
  ${BINDIR}/manifesttool -x ${VPNINSTPREFIX} ${VPNINSTPREFIX}/${DARTMANIFEST}
fi

# check the existence of the manifest file - if it does not exist, remove the manifesttool
if [ ! -f ${ACMANIFESTDAT} ] && [ -f ${BINDIR}/manifesttool ]; then
  echo "Removing ${BINDIR}/manifesttool" >> ${LOGFILE}
  rm -f ${BINDIR}/manifesttool
fi

for FILE in ${FILELIST}; do
  echo "rm -f ${FILE}" >> ${LOGFILE}
  rm -f ${FILE}
done

echo "Removing ${INSTPREFIX} folder" >> ${LOGFILE}
rm -rf ${INSTPREFIX} 

echo "Removing ${LEGACY_DARTBIN} folder" >> ${LOGFILE}
rm -rf ${LEGACY_DARTBIN} 

# update the menu cache so that the DART shortcut in the
# applications menu is removed. This is neccessary on some
# gnome desktops(Ubuntu 10.04)
if [ -x "/usr/share/gnome-menus/update-gnome-menus-cache" ]; then
    for CACHE_FILE in $(ls /usr/share/applications/desktop.*.cache); do
        echo "updating ${CACHE_FILE}" >> ${LOGFILE}
        /usr/share/gnome-menus/update-gnome-menus-cache /usr/share/applications/ > ${CACHE_FILE}
    done
fi

echo "Successfully removed Cisco AnyConnect Diagnostics and Reporting Tool from the system." >> ${LOGFILE}
echo "Successfully removed Cisco AnyConnect Diagnostics and Reporting Tool from the system."

exit 0
