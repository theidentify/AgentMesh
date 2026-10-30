// macOS notifications for Watch notices. UserNotifications needs a bundle identifier, so an unbundled `swift run`
// build skips them instead of crashing; the installed app (bundle.sh) posts them.
import ControlCore
import Foundation
import UserNotifications

@MainActor
final class Notifier {
    let enabled = Bundle.main.bundleIdentifier != nil

    func start() async {
        guard enabled else { return }
        _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])
    }

    func post(_ notice: Notice) {
        guard enabled else { return }
        let content = UNMutableNotificationContent()
        content.title = notice.title
        content.body = notice.body
        content.sound = .default
        content.threadIdentifier = "agentmesh." + notice.id  // groups a unit's notices together
        let request = UNNotificationRequest(identifier: "agentmesh.\(notice.id).\(UUID().uuidString)", content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }
}

/// Show banners even while the menu is open (the app is then active, and macOS would otherwise hold them back).
final class ForegroundBanners: NSObject, UNUserNotificationCenterDelegate {
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification)
        async -> UNNotificationPresentationOptions { [.banner, .sound] }
}
