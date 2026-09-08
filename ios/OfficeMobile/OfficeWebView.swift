import SwiftUI
import WebKit

struct OfficeWebView: UIViewRepresentable {
    @ObservedObject var connection: OfficeConnection
    let player: OfficePlayer
    func makeCoordinator() -> Coordinator { Coordinator(connection: connection, player: player) }
    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        config.userContentController.add(context.coordinator, name: "officeNative")
        config.userContentController.addUserScript(WKUserScript(source: "window.officeNativeAvailable=true", injectionTime: .atDocumentStart, forMainFrameOnly: true))
        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = context.coordinator; web.uiDelegate = context.coordinator
        web.isInspectable = true
        web.scrollView.isDirectionalLockEnabled = true
        web.scrollView.alwaysBounceHorizontal = false
        context.coordinator.web = web
        player.emit = { [weak coordinator = context.coordinator] data in coordinator?.emit(data) }
        return web
    }
    func updateUIView(_ web: WKWebView, context: Context) {
        guard let url = connection.url, context.coordinator.loadedURL != url else { return }
        context.coordinator.loadedURL = url; web.load(URLRequest(url: url))
    }
    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, WKDownloadDelegate {
        let connection: OfficeConnection
        let player: OfficePlayer
        weak var web: WKWebView?
        var loadedURL: URL?
        private var downloadURL: URL?
        init(connection: OfficeConnection, player: OfficePlayer) { self.connection = connection; self.player = player }
        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.frameInfo.isMainFrame, let origin = message.frameInfo.request.url,
                  let base = connection.url, sameOrigin(origin, base), let data = message.body as? [String: Any] else { return }
            switch data["command"] as? String {
            case "haptic": UIImpactFeedbackGenerator(style: .light).impactOccurred()
            case "configure": connection.configure = true
            default: player.handle(data, base: base)
            }
        }
        private func sameOrigin(_ url: URL, _ base: URL) -> Bool {
            url.scheme == base.scheme && url.host == base.host && url.port == base.port
        }
        func emit(_ data: [String: Any]) {
            guard let bytes = try? JSONSerialization.data(withJSONObject: data), let text = String(data: bytes, encoding: .utf8) else { return }
            web?.evaluateJavaScript("window.dispatchEvent(new CustomEvent('office-native-audio',{detail:\(text)}))", completionHandler: nil)
        }
        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { connection.error = nil }
        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            if (error as NSError).code != NSURLErrorCancelled { connection.error = "Your Mac is unreachable. Check Tailscale and retry." }
        }
        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = navigationAction.request.url, let base = connection.url else { decisionHandler(.cancel); return }
            if sameOrigin(url, base) { decisionHandler(navigationAction.shouldPerformDownload ? .download : .allow); return }
            if navigationAction.targetFrame?.isMainFrame == false { decisionHandler(.allow); return }
            if navigationAction.navigationType == .linkActivated, ["https", "http"].contains(url.scheme ?? "") { UIApplication.shared.open(url) }
            decisionHandler(.cancel)
        }
        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let url = navigationAction.request.url { UIApplication.shared.open(url) }
            return nil
        }
        func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
        func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
        func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
            decisionHandler(navigationResponse.canShowMIMEType ? .allow : .download)
        }
        func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
            let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            do {
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                let destination = folder.appendingPathComponent(URL(fileURLWithPath: suggestedFilename).lastPathComponent)
                downloadURL = destination; completionHandler(destination)
            } catch { connection.error = error.localizedDescription; completionHandler(nil) }
        }
        func downloadDidFinish(_ download: WKDownload) {
            guard let url = downloadURL, let scene = UIApplication.shared.connectedScenes.first as? UIWindowScene,
                  let presenter = scene.windows.first(where: \.isKeyWindow)?.rootViewController else { return }
            let share = UIActivityViewController(activityItems: [url], applicationActivities: nil)
            share.popoverPresentationController?.sourceView = presenter.view
            presenter.present(share, animated: true)
        }
        func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) { connection.error = error.localizedDescription }
    }
}
