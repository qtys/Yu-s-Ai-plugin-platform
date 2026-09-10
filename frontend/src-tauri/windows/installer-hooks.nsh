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
!macroend
