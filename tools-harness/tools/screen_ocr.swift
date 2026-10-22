// Apple Vision OCR: `screen_ocr <image>` prints recognized lines, one per line.
// Compiled on demand by tools/screen_watch.py (swiftc -O) — no pyobjc Vision needed.
import Foundation
import Vision
import CoreGraphics
import ImageIO

guard CommandLine.arguments.count > 1,
      let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: CommandLine.arguments[1]) as CFURL, nil),
      let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
    FileHandle.standardError.write(Data("cannot read image\n".utf8))
    exit(1)
}

let req = VNRecognizeTextRequest()
// `--accurate` (~12s) reads small blue link text the fast pass garbles.
req.recognitionLevel = CommandLine.arguments.contains("--accurate") ? .accurate : .fast
req.usesLanguageCorrection = false
try VNImageRequestHandler(cgImage: img, options: [:]).perform([req])
// `--boxes`: "text<TAB>x<TAB>y<TAB>w<TAB>h", normalized, origin top-left (for click targeting).
let boxes = CommandLine.arguments.contains("--boxes")
for obs in req.results ?? [] {
    guard let top = obs.topCandidates(1).first else { continue }
    if boxes {
        let b = obs.boundingBox
        print("\(top.string)\t\(b.minX)\t\(1 - b.maxY)\t\(b.width)\t\(b.height)")
    } else {
        print(top.string)
    }
}
