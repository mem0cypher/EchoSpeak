//go:build !windows
// +build !windows

package main

import "fmt"

func defaultWindowsMicName() (string, error) {
	return "", fmt.Errorf("default mic lookup not supported on this OS")
}
