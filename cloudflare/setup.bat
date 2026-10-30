@echo off
chcp 65001 >nul
title Claude Session Keeper - Cloudflare
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -Repo "%~1"
