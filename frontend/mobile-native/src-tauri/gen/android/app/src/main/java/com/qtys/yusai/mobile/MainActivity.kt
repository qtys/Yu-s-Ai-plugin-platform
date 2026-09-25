package com.qtys.yusai.mobile

import android.os.Bundle
import android.graphics.Color
import androidx.core.view.WindowCompat

class MainActivity : TauriActivity() {
  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    window.statusBarColor = Color.rgb(248, 245, 239)
    WindowCompat.getInsetsController(window, window.decorView).isAppearanceLightStatusBars = true
  }
}
