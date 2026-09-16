!macro StopYusAiProcesses
  DetailPrint "Stopping an existing Yu's AI instance..."
  ; New versions watch this file and flush their database/logs before exiting.
  IfFileExists "$INSTDIR\data\yus_ai.db" 0 +5
    FileOpen $2 "$INSTDIR\data\backend.shutdown" w
    FileWrite $2 "shutdown"
    FileClose $2
    Sleep 1500
  nsExec::ExecToStack '"$SYSDIR\taskkill.exe" /F /T /IM "yus-ai.exe"'
  Pop $0
  Pop $1
  nsExec::ExecToStack '"$SYSDIR\taskkill.exe" /F /T /IM "yus-ai-backend.exe"'
  Pop $0
  Pop $1
  Sleep 500
  Delete "$INSTDIR\data\backend.shutdown"
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro StopYusAiProcesses
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro StopYusAiProcesses
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Yus AI"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "Yus AI"
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Yus AI"
  ${If} $0 != ""
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Yus AI" '$\"$INSTDIR\yus-ai.exe$\"'
  ${EndIf}
!macroend
