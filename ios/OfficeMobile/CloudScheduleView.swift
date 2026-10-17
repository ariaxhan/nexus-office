import Foundation
#if canImport(UIKit)
import SafariServices
import UIKit
#endif

// The cloud scheduler page, opened in Safari's own in-app browser. Sign-in,
// status and receipts live on that page; this app never sees its cookies,
// tokens or outcome, so it reports nothing about them.
enum CloudSchedule {
    // Only the Office page on an explicit HTTPS host; a bare origin means /office/.
    static func url(from value: Any?) -> URL? {
        guard let text = (value as? String)?.trimmingCharacters(in: .whitespaces), !text.isEmpty,
              !text.contains("$("), var parts = URLComponents(string: text),
              parts.scheme?.lowercased() == "https", let host = parts.percentEncodedHost, !host.isEmpty,
              !host.contains("%"), parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil else { return nil }
        if parts.rangeOfPort != nil {
            guard let port = parts.port, (1...65535).contains(port) else { return nil }
        }
        switch parts.percentEncodedPath {
        case "", "/": parts.percentEncodedPath = "/office/"
        case "/office", "/office/": break
        default: return nil
        }
        return parts.url
    }
    static var configured: URL? { url(from: Bundle.main.object(forInfoDictionaryKey: "OfficeCloudURL")) }

#if canImport(UIKit)
    static func open() {
        guard let url = configured, let scene = UIApplication.shared.connectedScenes.first as? UIWindowScene,
              var presenter = scene.windows.first(where: \.isKeyWindow)?.rootViewController else { return }
        while let next = presenter.presentedViewController { presenter = next }
        guard !(presenter is SFSafariViewController) else { return }
        let safari = SFSafariViewController(url: url)
        safari.dismissButtonStyle = .done
        presenter.present(safari, animated: true)
    }
#endif
}
