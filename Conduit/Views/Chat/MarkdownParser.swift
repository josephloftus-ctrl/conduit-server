import Foundation

enum MarkdownBlock {
    case text(String)
    case codeBlock(language: String?, code: String)
}

struct MarkdownParser {
    static func parse(_ content: String) -> [MarkdownBlock] {
        var blocks: [MarkdownBlock] = []
        let pattern = "```(\\w*)\\n([\\s\\S]*?)```"

        guard let regex = try? NSRegularExpression(pattern: pattern, options: []) else {
            return [.text(content)]
        }

        let nsContent = content as NSString
        let matches = regex.matches(in: content, options: [], range: NSRange(location: 0, length: nsContent.length))

        var lastEnd = 0

        for match in matches {
            // Text before this code block
            let beforeRange = NSRange(location: lastEnd, length: match.range.location - lastEnd)
            if beforeRange.length > 0 {
                let beforeText = nsContent.substring(with: beforeRange)
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                if !beforeText.isEmpty {
                    blocks.append(.text(beforeText))
                }
            }

            // Language
            let languageRange = match.range(at: 1)
            let language = languageRange.length > 0 ? nsContent.substring(with: languageRange) : nil
            let effectiveLanguage = (language?.isEmpty ?? true) ? nil : language

            // Code content
            let codeRange = match.range(at: 2)
            let code = nsContent.substring(with: codeRange)

            blocks.append(.codeBlock(language: effectiveLanguage, code: code))

            lastEnd = match.range.location + match.range.length
        }

        // Remaining text after last code block
        if lastEnd < nsContent.length {
            let remainingText = nsContent.substring(from: lastEnd)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !remainingText.isEmpty {
                blocks.append(.text(remainingText))
            }
        }

        // If no blocks were created, treat entire content as text
        if blocks.isEmpty {
            blocks.append(.text(content))
        }

        return blocks
    }
}
