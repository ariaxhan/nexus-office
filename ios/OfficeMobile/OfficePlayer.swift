import Combine
import AVFoundation
import MediaPlayer
import UIKit

final class OfficePlayer: ObservableObject {
    private let player = AVPlayer()
    private var observer: Any?
    private var notifications: [NSObjectProtocol] = []
    private var readiness: NSKeyValueObservation?
    private var title = "Nexus Office"
    private var wantsPlayback = false
    private var speed: Float = 1
    private var episodeID = ""
    private var officeURL: URL?
    private var remember = true
    private var lastSave = Date.distantPast
    var background = false
    var emit: (([String: Any]) -> Void)?

    init() {
        observer = player.addPeriodicTimeObserver(forInterval: CMTime(seconds: 0.5, preferredTimescale: 600), queue: .main) { [weak self] _ in self?.tick() }
        remoteCommands()
        notifications.append(NotificationCenter.default.addObserver(forName: .AVPlayerItemDidPlayToEndTime, object: nil, queue: .main) { [weak self] note in
            guard let self, let item = note.object as? AVPlayerItem, item === self.player.currentItem else { return }
            self.pause()
        })
        notifications.append(NotificationCenter.default.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { [weak self] note in
            guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                  raw == AVAudioSession.InterruptionType.began.rawValue else { return }
            self?.pause()
        })
    }
    deinit {
        if let observer { player.removeTimeObserver(observer) }
        for token in notifications { NotificationCenter.default.removeObserver(token) }
    }

    func handle(_ data: [String: Any], base: URL) {
        guard let command = data["command"] as? String else { return }
        switch command {
        case "load": load(data, base: base)
        case "play": play()
        case "pause": pause()
        case "seek": seek(data["position"] as? Double ?? 0)
        case "rate": setRate(data["rate"] as? Float ?? 1)
        case "preferences": remember = data["remember"] as? Bool ?? true
        default: break
        }
    }
    private func load(_ data: [String: Any], base: URL) {
        guard let raw = data["url"] as? String, let url = URL(string: raw),
              url.scheme == base.scheme, url.host == base.host, url.port == base.port,
              url.path == "/api/media/content" else { return }
        officeURL = base; title = data["title"] as? String ?? "Nexus Office"
        episodeID = data["id"] as? String ?? ""; wantsPlayback = false
        let item = AVPlayerItem(url: url)
        readiness = item.observe(\.status, options: [.new]) { [weak self] item, _ in
            DispatchQueue.main.async {
                if item.status == .readyToPlay { self?.tick(ready: true) }
                if item.status == .failed { self?.emit?(["event": "error", "error": item.error?.localizedDescription ?? "Audio unavailable"]) }
            }
        }
        player.replaceCurrentItem(with: item)
    }
    func play() {
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
            try AVAudioSession.sharedInstance().setActive(true)
            wantsPlayback = true; player.playImmediately(atRate: speed); tick()
        } catch { emit?(["event": "error", "error": error.localizedDescription]) }
    }
    func pause() { wantsPlayback = false; player.pause(); tick() }
    func seek(_ seconds: Double) {
        guard seconds.isFinite else { return }
        player.seek(to: CMTime(seconds: max(0, seconds), preferredTimescale: 600)) { [weak self] _ in
            DispatchQueue.main.async { self?.tick() }
        }
    }
    private func setRate(_ value: Float) {
        speed = min(2, max(0.5, value)); if wantsPlayback { player.rate = speed }; tick()
    }
    private var position: Double { let value = player.currentTime().seconds; return value.isFinite ? value : 0 }
    private var duration: Double { let value = player.currentItem?.duration.seconds ?? 0; return value.isFinite ? value : 0 }
    private func tick(ready: Bool = false) {
        let state: [String: Any] = ["event": ready ? "loadedmetadata" : "timeupdate", "position": position,
                                    "duration": duration, "paused": !wantsPlayback, "rate": speed]
        emit?(state)
        MPNowPlayingInfoCenter.default().nowPlayingInfo = [MPMediaItemPropertyTitle: title,
            MPMediaItemPropertyArtist: "Nexus Office", MPMediaItemPropertyPlaybackDuration: duration,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: position, MPNowPlayingInfoPropertyPlaybackRate: wantsPlayback ? speed : 0]
        if background && remember && Date().timeIntervalSince(lastSave) > 10 { savePosition(); lastSave = Date() }
    }
    private func savePosition() {
        guard !episodeID.isEmpty, let base = officeURL,
              let url = URL(string: "/api/user-state", relativeTo: base)?.absoluteURL else { return }
        var request = URLRequest(url: url); request.httpMethod = "POST"; request.timeoutInterval = 10
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: ["kind": "listening", "id": episodeID,
            "value": position, "recorded_at": Date().timeIntervalSince1970 * 1000])
        URLSession.shared.dataTask(with: request).resume()
    }
    private func remoteCommands() {
        let commands = MPRemoteCommandCenter.shared()
        commands.playCommand.addTarget { [weak self] _ in self?.play(); return .success }
        commands.pauseCommand.addTarget { [weak self] _ in self?.pause(); return .success }
        commands.skipBackwardCommand.preferredIntervals = [15]
        commands.skipForwardCommand.preferredIntervals = [15]
        commands.skipBackwardCommand.addTarget { [weak self] _ in guard let self else { return .commandFailed }; self.seek(self.position - 15); return .success }
        commands.skipForwardCommand.addTarget { [weak self] _ in guard let self else { return .commandFailed }; self.seek(self.position + 15); return .success }
        commands.changePlaybackPositionCommand.addTarget { [weak self] event in
            guard let event = event as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
            self?.seek(event.positionTime); return .success
        }
    }
}
