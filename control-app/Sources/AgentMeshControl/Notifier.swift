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

    nonisolated static let projectCategory = "agentmesh.project"
    nonisolated static let scopePrefix = "scope:"

    func post(_ notice: Notice) {
        guard enabled else { return }
        let content = UNMutableNotificationContent()
        if let project = notice.project, !notice.scopes.isEmpty {
            // One button per scope; pressing one asks the supervisor, which raises its own Touch ID / password sheet.
            let actions = notice.scopes.map {
                UNNotificationAction(identifier: Self.scopePrefix + $0, title: scopeTitle($0), options: [.authenticationRequired])
            }
            UNUserNotificationCenter.current().setNotificationCategories(
                [UNNotificationCategory(identifier: Self.projectCategory, actions: actions, intentIdentifiers: [])])
            content.categoryIdentifier = Self.projectCategory
            content.userInfo = ["project": project]
        }
        content.title = notice.title
        content.body = notice.body
        content.sound = .default
        content.threadIdentifier = "agentmesh." + notice.id  // groups a unit's notices together
        let request = UNNotificationRequest(identifier: "agentmesh.\(notice.id).\(UUID().uuidString)", content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }
}

/// Shows banners even while the menu is open, and turns a scope button on a project notification into mapping.set.
final class ForegroundBanners: NSObject, UNUserNotificationCenterDelegate {
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification)
        async -> UNNotificationPresentationOptions { [.banner, .sound] }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        let action = response.actionIdentifier
        guard action.hasPrefix(Notifier.scopePrefix),
              let project = response.notification.request.content.userInfo["project"] as? String else { return }
        let scope = String(action.dropFirst(Notifier.scopePrefix.count))
        await Task { @MainActor in await StatusModel.shared.choose(scope, for: project) }.value
    }
}
