import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_android/webview_flutter_android.dart';
import 'package:flutter_web_auth_2/flutter_web_auth_2.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'config.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.dark,
    ),
  );

  final prefs = await SharedPreferences.getInstance();
  final token = prefs.getString('access_token');

  runApp(LumistyleApp(initialToken: token));
}

class LumistyleApp extends StatelessWidget {
  final String? initialToken;

  const LumistyleApp({super.key, this.initialToken});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: AppConfig.appName,
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF7C3AED)),
        useMaterial3: true,
      ),
      home: WebViewScreen(token: initialToken),
    );
  }
}

class WebViewScreen extends StatefulWidget {
  final String? token;
  const WebViewScreen({super.key, required this.token});

  @override
  State<WebViewScreen> createState() => _WebViewScreenState();
}

class _WebViewScreenState extends State<WebViewScreen> {
  late final WebViewController _controller;
  bool _isLoading = true;
  bool _hasError = false;
  double _loadingProgress = 0;
  String? _activeToken;
  bool _isAuthenticating = false;
  bool _webTokenSynced = false;

  static const _oauthHosts = {
    'accounts.google.com',
    'www.google.com',
    'google.com',
  };

  @override
  void initState() {
    super.initState();
    _activeToken = widget.token;
    _initWebView();
  }

  void _initWebView() {
    _controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(const Color(0xFF0F0F12))
      ..setNavigationDelegate(
        NavigationDelegate(
          onProgress: (progress) =>
              setState(() => _loadingProgress = progress / 100),
          onPageStarted: (url) => setState(() {
            _isLoading = true;
            _hasError = false;
          }),
          onPageFinished: (url) async {
            setState(() => _isLoading = false);
            await _captureTokenFromUrl(url);
            final uri = Uri.tryParse(url);
            final tokenInUrl = uri?.queryParameters['token'];
            if (tokenInUrl != null &&
                tokenInUrl.isNotEmpty &&
                !_webTokenSynced) {
              await _syncTokenToWebStorage(tokenInUrl);
              _webTokenSynced = true;
            }
            await _injectMobileBridge();
          },
          onWebResourceError: (_) => setState(() {
            _isLoading = false;
            _hasError = true;
          }),
          onNavigationRequest: _handleNavigation,
        ),
      )
      ..addJavaScriptChannel(
        'FlutterBridge',
        onMessageReceived: _handleWebMessage,
      );

    _setupAppCookie();
    _setupAndroidFilePicker();
    _loadAppUrl(_activeToken);
  }

  Future<void> _setupAndroidFilePicker() async {
    if (!Platform.isAndroid) return;
    final platform = _controller.platform;
    if (platform is! AndroidWebViewController) return;
    await platform.setOnShowFileSelector(_androidFilePicker);
  }

  Future<List<String>> _androidFilePicker(FileSelectorParams params) async {
    final wantsImagesOnly = params.acceptTypes.isEmpty ||
        params.acceptTypes.every(
          (type) => type.startsWith('image/') || type == 'image/*',
        );

    final result = await FilePicker.platform.pickFiles(
      allowMultiple: params.mode == FileSelectorMode.openMultiple,
      type: wantsImagesOnly ? FileType.image : FileType.any,
    );
    if (result == null) return [];

    return result.files
        .where((file) => file.path != null && file.path!.isNotEmpty)
        .map((file) => Uri.file(file.path!).toString())
        .toList();
  }

  Future<void> _setupAppCookie() async {
    final host = Uri.parse(AppConfig.webBaseUrl).host;
    if (host.isEmpty) return;
    await WebViewCookieManager().setCookie(
      WebViewCookie(
        name: 'viewappmobie',
        value: 'true',
        domain: host,
        path: '/',
      ),
    );
  }

  void _loadAppUrl(String? token, {String? nextPage}) {
    if (token != null && token.isNotEmpty) {
      final next = nextPage ?? 'analyze';
      final url = Uri.parse(AppConfig.webBaseUrl).replace(
        queryParameters: {
          'token': token,
          'next': next,
        },
      );
      _controller.loadRequest(url);
      return;
    }
    _controller.loadRequest(Uri.parse(AppConfig.webBaseUrl));
  }

  bool _isAllowedUrl(String url) {
    if (url.contains('token=') || url.contains('/api/auth/google/callback')) {
      return true;
    }
    if (url.startsWith(AppConfig.webBaseUrl)) return true;

    final host = Uri.tryParse(url)?.host ?? '';
    return _oauthHosts.contains(host);
  }

  NavigationDecision _handleNavigation(NavigationRequest request) {
    final url = request.url;

    if (url.contains('/api/auth/google')) {
      _triggerGoogleLogin();
      return NavigationDecision.prevent;
    }

    if (_isAllowedUrl(url)) {
      return NavigationDecision.navigate;
    }

    debugPrint('==> Chặn điều hướng ngoài: $url');
    return NavigationDecision.prevent;
  }

  void _handleWebMessage(JavaScriptMessage message) async {
    final data = message.message;
    debugPrint('==> Bridge: $data');

    switch (data) {
      case 'LOGOUT':
        await _processLogout();
        break;
      case 'GOOGLE_LOGIN':
        await _triggerGoogleLogin();
        break;
    }
  }

  Future<void> _triggerGoogleLogin() async {
    if (_isAuthenticating) return;
    _isAuthenticating = true;

    try {
      final result = await FlutterWebAuth2.authenticate(
        url: AppConfig.googleLoginMobileUrl,
        callbackUrlScheme: AppConfig.callbackScheme,
      );

      final uri = Uri.parse(result);
      final token = uri.queryParameters['token'];
      final error = uri.queryParameters['google_error'];

      if (token != null && token.isNotEmpty) {
        await _saveToken(token);
        _webTokenSynced = false;
        _loadAppUrl(token, nextPage: 'analyze');
      } else if (error != null) {
        debugPrint('==> Google OAuth error: $error');
        if (mounted) {
          _showAuthError(_googleErrorMessage(error));
        }
      }
    } catch (e) {
      debugPrint('==> Google login cancelled/failed: $e');
      if (mounted) {
        _showAuthError('Đăng nhập Google bị hủy hoặc thất bại. Kiểm tra kết nối và cấu hình OAuth trên server.');
      }
    } finally {
      _isAuthenticating = false;
    }
  }

  String _googleErrorMessage(String code) {
    switch (code) {
      case 'config':
        return 'Chưa cấu hình Google OAuth trên server (GOOGLE_CLIENT_ID/SECRET).';
      case 'token':
        return 'Google từ chối đăng nhập. Kiểm tra GOOGLE_REDIRECT_URI khớp Google Cloud Console.';
      case 'no_sub':
        return 'Không nhận được thông tin tài khoản từ Google.';
      case 'db':
        return 'Lỗi lưu tài khoản trên server.';
      default:
        return 'Đăng nhập Google thất bại ($code).';
    }
  }

  void _showAuthError(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        duration: const Duration(seconds: 5),
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  Future<void> _captureTokenFromUrl(String url) async {
    final uri = Uri.tryParse(url);
    final token = uri?.queryParameters['token'];
    if (token != null && token.isNotEmpty && token != _activeToken) {
      await _saveToken(token);
    }
  }

  Future<void> _processLogout() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('access_token');

    await WebViewCookieManager().clearCookies();
    await _setupAppCookie();

    setState(() => _activeToken = null);
    _webTokenSynced = false;
    _loadAppUrl(null);
  }

  Future<void> _saveToken(String token) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('access_token', token);
    setState(() => _activeToken = token);
  }

  Future<void> _syncTokenToWebStorage(String token) async {
    final safeToken = token.replaceAll('\\', r'\\').replaceAll("'", r"\'");
    await _controller.runJavaScript('''
      try {
        localStorage.setItem('access_token', '$safeToken');
      } catch(e) {}
    ''');
  }

  Future<void> _injectMobileBridge() async {
    await _controller.runJavaScript('''
      (function() {
        document.querySelectorAll('a[href*="/api/auth/google"]').forEach(function(a) {
          if (a.dataset.flutterBound) return;
          a.dataset.flutterBound = '1';
          a.addEventListener('click', function(e) {
            e.preventDefault();
            if (window.FlutterBridge) FlutterBridge.postMessage('GOOGLE_LOGIN');
            else window.location.href = a.href;
          });
        });
      })();
    ''');
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) async {
        if (didPop) return;
        if (await _controller.canGoBack()) {
          _controller.goBack();
        } else if (context.mounted) {
          SystemNavigator.pop();
        }
      },
      child: Scaffold(
        backgroundColor: const Color(0xFF0F0F12),
        body: Stack(
          children: [
            if (!_hasError)
              WebViewWidget(controller: _controller)
            else
              _ErrorView(onRetry: () => _controller.reload()),
            if (_isLoading && !_hasError) _buildProgressBar(),
          ],
        ),
      ),
    );
  }

  Widget _buildProgressBar() {
    return Positioned(
      top: 0,
      left: 0,
      right: 0,
      child: LinearProgressIndicator(
        value: _loadingProgress,
        backgroundColor: Colors.transparent,
        color: const Color(0xFF7C3AED),
        minHeight: 3,
      ),
    );
  }
}

class _ErrorView extends StatelessWidget {
  final VoidCallback onRetry;
  const _ErrorView({required this.onRetry});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colorScheme = theme.colorScheme;

    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
              Icons.cloud_off_rounded,
              size: 80,
              color: colorScheme.primary.withOpacity(0.6),
            ),
            const SizedBox(height: 24),
            Text(
              'Không kết nối được server',
              style: theme.textTheme.headlineSmall?.copyWith(
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 12),
            Text(
              'Kiểm tra Flask đang chạy (python app.py) và URL trong lib/config.dart.\n'
              'Emulator: http://10.0.2.2:5000\n'
              'Điện thoại thật: http://<IP-máy-tính>:5000',
              textAlign: TextAlign.center,
              style: theme.textTheme.bodyMedium?.copyWith(
                color: colorScheme.onSurfaceVariant,
              ),
            ),
            const SizedBox(height: 32),
            ElevatedButton.icon(
              onPressed: onRetry,
              icon: const Icon(Icons.refresh_rounded),
              label: const Text('Thử lại'),
            ),
          ],
        ),
      ),
    );
  }
}
