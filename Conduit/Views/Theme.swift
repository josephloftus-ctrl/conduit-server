import SwiftUI
import UIKit

// MARK: - Adaptive Color Helper

extension Color {
    init(light: UIColor, dark: UIColor) {
        self.init(uiColor: UIColor { traits in
            traits.userInterfaceStyle == .dark ? dark : light
        })
    }
}

// MARK: - Conduit Brand Colors

extension Color {
    static let conduitAccent = Color(
        light: UIColor(red: 0.761, green: 0.584, blue: 0.227, alpha: 1), // #C2953A
        dark: UIColor(red: 0.831, green: 0.667, blue: 0.333, alpha: 1)   // #D4AA55
    )

    static let conduitBackground = Color(
        light: UIColor(red: 0.980, green: 0.969, blue: 0.949, alpha: 1), // #FAF7F2
        dark: UIColor(red: 0.165, green: 0.176, blue: 0.196, alpha: 1)   // #2A2D32
    )

    static let conduitBackgroundSecondary = Color(
        light: UIColor(red: 0.957, green: 0.933, blue: 0.894, alpha: 1), // #F4EEE4
        dark: UIColor(red: 0.137, green: 0.145, blue: 0.165, alpha: 1)   // #23252A
    )

    static let conduitSuccess = Color(
        light: UIColor(red: 0.298, green: 0.624, blue: 0.322, alpha: 1), // #4C9F52
        dark: UIColor(red: 0.392, green: 0.737, blue: 0.416, alpha: 1)   // #64BC6A
    )

    static let conduitWarning = Color(
        light: UIColor(red: 0.800, green: 0.627, blue: 0.204, alpha: 1), // #CCA034
        dark: UIColor(red: 0.863, green: 0.706, blue: 0.275, alpha: 1)   // #DCB446
    )

    static let conduitError = Color(
        light: UIColor(red: 0.769, green: 0.333, blue: 0.267, alpha: 1), // #C45544
        dark: UIColor(red: 0.847, green: 0.424, blue: 0.361, alpha: 1)   // #D86C5C
    )

    static let conduitUserBubble = Color(
        light: UIColor(red: 0.761, green: 0.584, blue: 0.227, alpha: 1), // #C2953A
        dark: UIColor(red: 0.698, green: 0.533, blue: 0.204, alpha: 1)   // #B28834
    )

    static let conduitInactive = Color(
        light: UIColor(red: 0.635, green: 0.620, blue: 0.588, alpha: 1), // #A29E96
        dark: UIColor(red: 0.463, green: 0.451, blue: 0.431, alpha: 1)   // #76736E
    )

    static let conduitCodeBackground = Color(
        light: UIColor(red: 0.941, green: 0.929, blue: 0.914, alpha: 1), // #F0EDEA warm light gray
        dark: UIColor(red: 0.118, green: 0.125, blue: 0.141, alpha: 1)   // #1E2024 near-black
    )
}

// MARK: - Background Modifier

extension View {
    func conduitBackground() -> some View {
        self.background(
            LinearGradient(
                colors: [.conduitBackground, .conduitBackgroundSecondary],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()
        )
    }
}
