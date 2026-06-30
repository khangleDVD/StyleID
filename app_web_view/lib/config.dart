// ============================================================
// Cấu hình Flutter app — LumiStyle (nhận dạng thời trang)
// ============================================================

class AppConfig {
  /// URL web production (Vercel) — không có dấu / ở cuối.
  static const String webBaseUrl = 'https://224817-styleid.vercel.app';

  /// Flask dùng chung origin cho web + API
  static const String apiBaseUrl = webBaseUrl;

  /// Deep link sau Google OAuth mobile (khớp AndroidManifest.xml)
  static const String callbackScheme = 'lumistyle';

  static const String appName = 'StyleID';
  static const String appVersion = '1.0.4';

  static String get googleLoginMobileUrl =>
      '$apiBaseUrl/api/auth/google?mobile=1';
}
