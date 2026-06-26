// ============================================================
// Cấu hình Flutter app — LumiStyle (nhận dạng thời trang)
// ============================================================

class AppConfig {
  /// URL Flask (không có dấu / ở cuối).
  /// Production qua ngrok
  static const String webBaseUrl ='https://resume-washroom-chafe.ngrok-free.dev';

  /// Flask dùng chung origin cho web + API
  static const String apiBaseUrl = webBaseUrl;

  /// Deep link sau Google OAuth mobile (khớp AndroidManifest.xml)
  static const String callbackScheme = 'lumistyle';

  static const String appName = 'LumiStyle';
  static const String appVersion = '1.0.1';

  static String get googleLoginMobileUrl =>
      '$apiBaseUrl/api/auth/google?mobile=1';
}
