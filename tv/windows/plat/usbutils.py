# Miro - an RSS based video player application
# Copyright (C) 2010, 2011
# Participatory Culture Foundation
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA
#
# In addition, as a special exception, the copyright holders give
# permission to link the code of portions of this program with the OpenSSL
# library.
#
# You must obey the GNU General Public License in all respects for all of
# the code used other than OpenSSL. If you modify file(s) with this
# exception, you may extend this exception to your version of the file(s),
# but you are not obligated to do so. If you do not wish to do so, delete
# this exception statement from your version. If you delete this exception
# statement from all source files in the program, then also delete it here.

import logging
import ctypes, ctypes.wintypes
import _winreg

LOTS_OF_DEBUGGING = False

def warn(what, code, message):
    logging.warn('error doing %s (%d): %s', what, code, message)

INVALID_HANDLE_VALUE = -1
DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_NO_MORE_ITEMS = 259
MAXIMUM_USB_STRING_LENGTH = 255

kernel32 = ctypes.windll.kernel32

setupapi = ctypes.windll.setupapi
SetupDiGetClassDevs = setupapi.SetupDiGetClassDevsW
SetupDiEnumDeviceInterfaces = setupapi.SetupDiEnumDeviceInterfaces
SetupDiGetDeviceInterfaceDetail = setupapi.SetupDiGetDeviceInterfaceDetailW

CM_Get_Parent = setupapi.CM_Get_Parent
CM_Get_Device_ID = setupapi.CM_Get_Device_IDW
CM_Request_Device_Eject = setupapi.CM_Request_Device_EjectW

class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]

    def __str__(self):
        return '{%08X-%04X-%04X-%04X-%012X}' % (
            self.Data1, self.Data2, self.Data3,
            self.Data4[0] * 256 + self.Data4[1],
            self.Data4[2] * (256 ** 5) +
            self.Data4[3] * (256 ** 4) +
            self.Data4[4] * (256 ** 3) +
            self.Data4[5] * (256 ** 2) +
            self.Data4[6] * 256 +
            self.Data4[7])

class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.DWORD),
            ("ClassGuid", GUID),
            ("DevInst", ctypes.wintypes.DWORD),
            ("Reserved", ctypes.c_void_p)
            ]

class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.DWORD),
            ("InterfaceClassGuid", GUID),
            ("Flags", ctypes.wintypes.DWORD),
            ("Reserved", ctypes.c_void_p)
            ]

class SP_DEVICE_INTERFACE_DETAIL_DATA(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.DWORD),
            ("DevicePath", ctypes.c_wchar*255)]

GUID_DEVINTERFACE_VOLUME = GUID(0x53F5630D, 0xB6BF, 0x11D0,
        (ctypes.c_ubyte*8)(0x94, 0xF2, 0x00, 0xA0, 0xC9, 0x1E, 0xFB, 0x8B))

hDevInfo = None

def get_class_devs():
    global hDevInfo
    hDevInfo = SetupDiGetClassDevs(ctypes.byref(GUID_DEVINTERFACE_VOLUME),
                                   0,
                                   0,
                                   DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if hDevInfo == INVALID_HANDLE_VALUE:
        warn('get_class_devs', ctypes.windll.GetLastError(),
             ctypes.windll.FormatError())

def get_device_interface(i, device=None):
    interfaceData = SP_DEVICE_INTERFACE_DATA()
    interfaceData.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
    if SetupDiEnumDeviceInterfaces(
        hDevInfo,
        device and ctypes.byref(device) or None,
        ctypes.byref(GUID_DEVINTERFACE_VOLUME),
        i,
        ctypes.byref(interfaceData)):
        return interfaceData
    elif ctypes.GetLastError() == ERROR_NO_MORE_ITEMS:
        return
    else:
        warn('get_device_interface', ctypes.GetLastError(),
             ctypes.windll.FormatError())

def get_device_interface_detail(interface):
    detail = None
    size = 0
    length = ctypes.wintypes.DWORD(0)
    device = SP_DEVINFO_DATA(cbSize=ctypes.sizeof(SP_DEVINFO_DATA))
    while not SetupDiGetDeviceInterfaceDetail(
        hDevInfo,
        ctypes.byref(interface),
        detail and ctypes.byref(detail) or None,
        size,
        ctypes.byref(length),
        ctypes.byref(device)
        ):
        if ctypes.GetLastError() == ERROR_INSUFFICIENT_BUFFER:
            size = length.value
            detail = SP_DEVICE_INTERFACE_DETAIL_DATA(
                cbSize=6)
        else:
            warn('get_device_interface_detail', ctypes.windll.GetLastError(),
                 ctypes.windll.FormatError())
            return
    return detail.DevicePath, device

def device_eject(devInst):
    CM_Request_Device_Eject(devInst, None, None, 0, 0)

def get_parent(devInst):
    parent = ctypes.wintypes.DWORD(0)
    CM_Get_Parent(ctypes.byref(parent), devInst, 0)
    return parent.value

def get_device_id(devInst):
    buffer = ctypes.create_unicode_buffer(255)
    CM_Get_Device_ID(devInst, ctypes.byref(buffer), 255, 0)
    return buffer.value

def get_volume_name(mount_point):
    buffer = ctypes.create_unicode_buffer(50)
    kernel32.GetVolumeNameForVolumeMountPointW(mount_point,
                                               ctypes.byref(buffer), 50)
    return buffer.value

def get_path_name(volume):
    buffer = ctypes.create_unicode_buffer(255)
    length = ctypes.wintypes.DWORD(0)
    kernel32.GetVolumePathNamesForVolumeNameW(volume, ctypes.byref(buffer),
                                              255, ctypes.byref(length))
    return buffer.value

def connected_devices():
    """
    Returns a generator which returns small dictionaries of data
    representing the connected USB storage devices.
    """
    get_class_devs() # reset the device class to pick up all devices
    interface_index = 0
    while True:
        interface = get_device_interface(interface_index)
        if interface is None:
            break
        interface_index += 1 # loop through the interfaces
        path, device = get_device_interface_detail(interface)
        device_id = get_device_id(device.DevInst)
        if LOTS_OF_DEBUGGING:
            logging.debug('connected_devices(): %i %r %r',
                          interface_index, path, device_id)
        if '_??_USBSTOR' in device_id:
            """Looks like:
STORAGE\VOLUME\_??_USBSTOR#DISK&VEN_KINGSTON&PROD_DATATRAVELER_G3&REV_PMAP#\
001372982D6AEAC18576014E&0#{53F56307-B6BF-11D0-94F2-00A0C91EFB8B}"""
            reg_key = '\\'.join(device_id.split('_??_')[1].split('#')[:3])
        else:
            deviceParent = get_parent(device.DevInst)
            reg_key = get_device_id(deviceParent)
            if LOTS_OF_DEBUGGING:
                logging.debug('parent id: %r', reg_key)
            if not reg_key.startswith('USBSTOR'):
                # not a USB storage device
                continue
        volume_name = get_volume_name(path + '\\')
        drive_name = get_path_name(volume_name)
        if LOTS_OF_DEBUGGING:
            logging.debug('volume/drive name: %r/%r',
                          volume_name, drive_name)
        with _winreg.OpenKey(
            _winreg.HKEY_LOCAL_MACHINE,
            'SYSTEM\\CurrentControlSet\\Enum\\%s' % reg_key) as k:
            # pull the USB Name out of the registry
            index = 0
            friendly_name = None
            while True:
                try:
                    name, value, type_ = _winreg.EnumValue(k, index)
                except WindowsError:
                    break
                if name == 'FriendlyName':
                    # blah blah USB Device
                    friendly_name = value[:-len(' USB Device')]
                    break
                else:
                    index += 1
            if not friendly_name:
                continue
        yield {
            'volume': volume_name,
            'mount': drive_name,
            'name': friendly_name,
            }

if __name__ == '__main__':
    for d in connected_devices():
        print d
