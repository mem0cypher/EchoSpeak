//go:build windows
// +build windows

package main

import (
	"fmt"

	"github.com/go-ole/go-ole"
	"github.com/moutend/go-wca/pkg/wca"
)

func defaultWindowsMicName() (string, error) {
	if err := ole.CoInitializeEx(0, ole.COINIT_APARTMENTTHREADED); err != nil {
		if !isOleSFalse(err) {
			return "", err
		}
	}
	defer ole.CoUninitialize()

	var mmde *wca.IMMDeviceEnumerator
	if err := wca.CoCreateInstance(wca.CLSID_MMDeviceEnumerator, 0, wca.CLSCTX_ALL, wca.IID_IMMDeviceEnumerator, &mmde); err != nil {
		return "", err
	}
	defer mmde.Release()

	var mmd *wca.IMMDevice
	if err := mmde.GetDefaultAudioEndpoint(wca.ECapture, wca.EConsole, &mmd); err != nil {
		return "", err
	}
	defer mmd.Release()

	var ps *wca.IPropertyStore
	if err := mmd.OpenPropertyStore(wca.STGM_READ, &ps); err != nil {
		return "", err
	}
	defer ps.Release()

	var pv wca.PROPVARIANT
	if err := ps.GetValue(&wca.PKEY_Device_FriendlyName, &pv); err != nil {
		return "", err
	}
	name := pv.String()
	if name == "" {
		return "", fmt.Errorf("empty default mic name")
	}
	return name, nil
}

func isOleSFalse(err error) bool {
	if err == nil {
		return false
	}
	oleErr, ok := err.(*ole.OleError)
	if !ok {
		return false
	}
	return oleErr.Code() == uintptr(0x00000001)
}
