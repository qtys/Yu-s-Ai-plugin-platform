!macro StopYusAiProcesses
  DetailPrint "Stopping an existing Yu's AI instance..."
  nsExec::ExecToStack '"$SYSDIR\taskkill.exe" /F /T /IM "yus-ai.exe"'
  Pop $0
  Pop $1
  nsExec::ExecToStack '"$SYSDIR\taskkill.exe" /F /T /IM "yus-ai-backend.exe"'
  Pop $0
  Pop $1
  Sleep 500
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
