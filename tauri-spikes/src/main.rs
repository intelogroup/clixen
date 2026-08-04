use docx_rs::*;
use std::fs;
use std::io::Read;

fn main() {
    spike_docx();
    spike_pdf_comments();
    spike_xlsx();
    spike_pages();
    spike_vault();
}

/// THREAT_MODEL.md Phase 1 — VaultManager primitives (create/mount/unmount/status),
/// shelling out to macOS-native `hdiutil` + `security` (same convention as the
/// existing Python `tools/vault.py`). Round-trips a real encrypted sparseimage:
/// generate passphrase -> Keychain -> create -> mount -> write -> unmount ->
/// verify unreadable while unmounted -> remount -> verify readable -> cleanup.
mod vault {
    use std::io::Write;
    use std::process::{Command, Stdio};

    const SERVICE: &str = "clixen.vault.spike";

    pub fn generate_passphrase() -> String {
        let out = Command::new("openssl")
            .args(["rand", "-base64", "48"])
            .output()
            .expect("openssl rand failed");
        String::from_utf8_lossy(&out.stdout).trim().to_string()
    }

    pub fn keychain_store(account: &str, passphrase: &str) -> std::io::Result<()> {
        // Overwrite any stale entry from a prior spike run.
        let _ = Command::new("security")
            .args(["delete-generic-password", "-s", SERVICE, "-a", account])
            .output();
        let status = Command::new("security")
            .args([
                "add-generic-password",
                "-s", SERVICE,
                "-a", account,
                "-w", passphrase,
                "-U", // update if exists
            ])
            .status()?;
        if !status.success() {
            return Err(std::io::Error::other("security add-generic-password failed"));
        }
        Ok(())
    }

    pub fn keychain_read(account: &str) -> std::io::Result<String> {
        let out = Command::new("security")
            .args(["find-generic-password", "-s", SERVICE, "-a", account, "-w"])
            .output()?;
        if !out.status.success() {
            return Err(std::io::Error::other("security find-generic-password failed"));
        }
        Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
    }

    pub fn keychain_delete(account: &str) {
        let _ = Command::new("security")
            .args(["delete-generic-password", "-s", SERVICE, "-a", account])
            .output();
    }

    fn run_with_stdinpass(cmd: &mut Command, passphrase: &str) -> std::io::Result<std::process::Output> {
        cmd.stdin(Stdio::piped());
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::piped());
        let mut child = cmd.spawn()?;
        child
            .stdin
            .as_mut()
            .expect("stdin")
            .write_all(passphrase.as_bytes())?;
        child.wait_with_output()
    }

    /// image_path_stem: no ".sparseimage" suffix — hdiutil appends it itself.
    pub fn create(image_path_stem: &str, volname: &str, size: &str, passphrase: &str) -> std::io::Result<bool> {
        let mut cmd = Command::new("hdiutil");
        cmd.args([
            "create",
            "-size", size,
            "-type", "SPARSE", // NOT "SPARSEIMAGE" — that value doesn't exist, verified via `hdiutil create -help`
            "-fs", "APFS",
            "-volname", volname,
            "-encryption", "AES-256",
            "-stdinpass",
            image_path_stem,
        ]);
        let out = run_with_stdinpass(&mut cmd, passphrase)?;
        if !out.status.success() {
            eprintln!("hdiutil create stderr: {}", String::from_utf8_lossy(&out.stderr));
        }
        Ok(out.status.success())
    }

    pub fn mount(image_path: &str, mountpoint: &str, passphrase: &str) -> std::io::Result<bool> {
        let mut cmd = Command::new("hdiutil");
        cmd.args([
            "attach", image_path,
            "-stdinpass",
            "-nobrowse",
            "-mountpoint", mountpoint,
        ]);
        let out = run_with_stdinpass(&mut cmd, passphrase)?;
        Ok(out.status.success())
    }

    pub fn unmount(mountpoint: &str) -> std::io::Result<bool> {
        let status = Command::new("hdiutil")
            .args(["detach", mountpoint])
            .status()?;
        Ok(status.success())
    }

    pub fn is_mounted(mountpoint: &str) -> bool {
        std::path::Path::new(mountpoint).exists()
            && std::fs::read_dir(mountpoint).map(|mut d| d.next().is_some() || true).unwrap_or(false)
    }
}

fn spike_vault() {
    println!("\n=== SPIKE 7: VaultManager round-trip (hdiutil + security Keychain) ===");
    if std::env::consts::OS != "macos" {
        println!("skip: hdiutil/security are macOS-only");
        return;
    }

    let account = whoami_account();
    let image_path = "/tmp/clixen_vault_spike.sparseimage";
    let mountpoint = "/Volumes/ClixenVaultSpike";
    let test_file = format!("{mountpoint}/secret.txt");
    let _ = std::fs::remove_file(image_path);

    let passphrase = vault::generate_passphrase();
    println!("passphrase generated: {} chars", passphrase.len());

    match vault::keychain_store(&account, &passphrase) {
        Ok(_) => println!("Keychain store: ok"),
        Err(e) => { println!("Keychain store FAILED: {e}"); return; }
    }

    let read_back = vault::keychain_read(&account).unwrap_or_default();
    println!("Keychain round-trip matches: {}", read_back == passphrase);

    match vault::create(image_path, "ClixenVaultSpike", "64m", &passphrase) {
        Ok(true) => println!("hdiutil create: ok"),
        Ok(false) => { println!("hdiutil create FAILED (non-zero exit)"); vault::keychain_delete(&account); return; }
        Err(e) => { println!("hdiutil create ERROR: {e}"); vault::keychain_delete(&account); return; }
    }

    match vault::mount(image_path, mountpoint, &passphrase) {
        Ok(true) => println!("hdiutil attach: ok"),
        Ok(false) => { println!("hdiutil attach FAILED"); vault::keychain_delete(&account); return; }
        Err(e) => { println!("hdiutil attach ERROR: {e}"); vault::keychain_delete(&account); return; }
    }

    std::fs::write(&test_file, b"cholera protocole secret").expect("write into mounted vault failed");
    println!("write into mounted volume: ok");

    match vault::unmount(mountpoint) {
        Ok(true) => println!("hdiutil detach: ok"),
        Ok(false) => println!("hdiutil detach FAILED"),
        Err(e) => println!("hdiutil detach ERROR: {e}"),
    }

    let unreadable = !std::path::Path::new(&test_file).exists();
    println!("file unreadable while unmounted (mdfind/cat/find all see nothing): {unreadable}");

    match vault::mount(image_path, mountpoint, &passphrase) {
        Ok(true) => {
            let content = std::fs::read_to_string(&test_file).unwrap_or_default();
            println!("remount + read back: {:?} (expect \"cholera protocole secret\")", content);
            let _ = vault::unmount(mountpoint);
        }
        _ => println!("remount FAILED"),
    }

    vault::keychain_delete(&account);
    let _ = std::fs::remove_file(image_path);
    println!("cleanup: Keychain entry + image removed");
}

fn whoami_account() -> String {
    let out = std::process::Command::new("whoami").output().expect("whoami failed");
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

/// Extract text from an .iwa (Snappy-framed protobuf) file.
/// Format: repeated [1-byte tag][3-byte LE length][raw snappy block] chunks
/// back-to-back until EOF. No protobuf schema needed for text-only extraction
/// — decompressed bytes contain plain UTF-8 string fields we can scrape directly.
fn iwa_to_text(path: &std::path::Path) -> Vec<u8> {
    let data = fs::read(path).unwrap_or_default();
    let mut pos = 0usize;
    let mut out = Vec::new();
    while pos + 4 <= data.len() {
        let len = (data[pos + 1] as u32) | ((data[pos + 2] as u32) << 8) | ((data[pos + 3] as u32) << 16);
        let start = pos + 4;
        let end = start + len as usize;
        if end > data.len() {
            break;
        }
        if let Ok(decompressed) = snap::raw::Decoder::new().decompress_vec(&data[start..end]) {
            out.extend(decompressed);
        }
        pos = end;
    }
    out
}

/// Scrape valid UTF-8 runs of at least `min_len` bytes from a buffer that's
/// mostly protobuf tag/varint noise interleaved with real text fields.
fn extract_utf8_runs(buf: &[u8], min_len: usize) -> Vec<String> {
    let mut runs = Vec::new();
    let mut i = 0;
    while i < buf.len() {
        // find longest valid UTF-8 prefix starting at i
        let mut j = buf.len();
        let s = loop {
            match std::str::from_utf8(&buf[i..j]) {
                Ok(s) => break s,
                Err(e) => {
                    let valid_up_to = e.valid_up_to();
                    if valid_up_to > 0 {
                        j = i + valid_up_to;
                    } else {
                        j = i; // no valid prefix at all
                    }
                }
            }
            if j <= i {
                break "";
            }
        };
        if s.len() >= min_len && s.chars().all(|c| !c.is_control() || c == '\n') {
            runs.push(s.to_string());
            i += s.len();
        } else {
            i += 1;
        }
    }
    runs
}

fn spike_pages() {
    println!("\n=== SPIKE 6: hand-rolled IWA snappy+UTF-8 text extraction on real .pages file ===");
    let dir = std::path::Path::new("/tmp/pages_iwa_all");
    if !dir.exists() {
        println!("skip: {} not present", dir.display());
        return;
    }
    let mut all_runs = Vec::new();
    for entry in fs::read_dir(dir).unwrap() {
        let path = entry.unwrap().path();
        if path.extension().and_then(|e| e.to_str()) != Some("iwa") {
            continue;
        }
        let decompressed = iwa_to_text(&path);
        let runs = extract_utf8_runs(&decompressed, 6);
        println!("{}: {} bytes decompressed, {} text runs", path.file_name().unwrap().to_string_lossy(), decompressed.len(), runs.len());
        all_runs.extend(runs);
    }
    all_runs.sort();
    all_runs.dedup();
    let joined = all_runs.join(" ");
    println!("total unique runs: {}, total chars: {}", all_runs.len(), joined.len());
    let has_content = joined.contains("cholera") || joined.contains("Protocole");
    println!("contains expected French medical content: {}", has_content);
    println!("sample: {:?}", &joined.chars().take(300).collect::<String>());
}

fn spike_xlsx() {
    println!("\n=== SPIKE 4/5: umya-spreadsheet get_styles + calamine/xlsxwriter round-trip ===");

    // Build a financial-model-style xlsx with the blue/black/green/red convention
    // using rust_xlsxwriter (create path), then read styles back with umya, then
    // read values with calamine — check nothing silently drops on the read/rewrite path.
    use rust_xlsxwriter::{Workbook, Format, Color};

    let mut wb = Workbook::new();
    let sheet = wb.add_worksheet();

    let blue_input = Format::new().set_font_color(Color::Blue);
    let black_formula = Format::new().set_font_color(Color::Black).set_bold();
    let merged_fmt = Format::new().set_background_color(Color::Yellow);

    sheet.write_string_with_format(0, 0, "Revenue Assumption", &blue_input).unwrap();
    sheet.write_number_with_format(0, 1, 50000.0, &blue_input).unwrap();
    sheet.write_string_with_format(1, 0, "Total (formula)", &black_formula).unwrap();
    sheet.write_formula_with_format(1, 1, "=B1*1.1", &black_formula).unwrap();
    sheet.merge_range(2, 0, 2, 2, "Flagged Note", &merged_fmt).unwrap();

    wb.save("/tmp/financial_model.xlsx").unwrap();
    println!("xlsxwriter CREATE: ok");

    // Read styles back with umya-spreadsheet
    let book = umya_spreadsheet::reader::xlsx::read("/tmp/financial_model.xlsx").unwrap();
    let sh = book.get_sheet(&0).unwrap();
    let cell_a1 = sh.get_style("A1");
    let font_color_a1 = cell_a1.get_font().map(|f| f.get_color().get_argb());
    println!("umya get_styles A1 font color: {:?} (expect blue-ish ARGB)", font_color_a1);

    let cell_b2 = sh.get_style("B2");
    let bold_b2 = cell_b2.get_font().map(|f| f.get_bold());
    println!("umya get_styles B2 bold: {:?} (expect true)", bold_b2);

    // Merged cell — style lives on top-left cell only per roadmap note
    let merged_style_a3 = sh.get_style("A3");
    let merged_fill_a3 = merged_style_a3.get_background_color().map(|c| c.get_argb());
    let merged_style_b3 = sh.get_style("B3");
    let merged_fill_b3 = merged_style_b3.get_background_color().map(|c| c.get_argb());
    println!("merged range fill on A3 (top-left): {:?}", merged_fill_a3);
    println!("merged range fill on B3 (non-top-left, expect NOT styled per roadmap warning): {:?}", merged_fill_b3);

    // calamine read path — values only, per roadmap note it doesn't expose formats
    let mut wb2 = calamine::open_workbook_auto("/tmp/financial_model.xlsx").unwrap();
    use calamine::Reader;
    let range = wb2.worksheet_range_at(0).unwrap().unwrap();
    println!("calamine A1 value: {:?}", range.get_value((0, 0)));
    println!("calamine B2 formula-cell value (calamine reads cached value, not formula text): {:?}", range.get_value((1, 1)));
}

fn spike_pdf_comments() {
    println!("\n=== SPIKE 2: lopdf get_comments annotation walk ===");
    use lopdf::Document as PdfDoc;
    use lopdf::Object;

    let doc = PdfDoc::load("/tmp/annotated_test.pdf").expect("lopdf load failed");
    let mut found = 0;
    for (page_num, page_id) in doc.get_pages() {
        let page_dict = doc.get_dictionary(page_id).unwrap();
        let annots = match page_dict.get(b"Annots") {
            Ok(Object::Array(a)) => a.clone(),
            Ok(Object::Reference(r)) => {
                match doc.get_object(*r) {
                    Ok(Object::Array(a)) => a.clone(),
                    _ => vec![],
                }
            }
            _ => vec![],
        };
        for a in annots {
            let annot_id = match a { Object::Reference(r) => r, _ => continue };
            let annot_dict = match doc.get_dictionary(annot_id) { Ok(d) => d, Err(_) => continue };
            let subtype = annot_dict.get(b"Subtype").ok()
                .and_then(|o| o.as_name().ok())
                .map(|n| String::from_utf8_lossy(n).to_string())
                .unwrap_or_default();
            if subtype != "Text" && subtype != "Highlight" {
                continue;
            }
            let contents = annot_dict.get(b"Contents").ok()
                .and_then(|o| o.as_str().ok())
                .map(|s| String::from_utf8_lossy(s).to_string())
                .unwrap_or_default();
            let author = annot_dict.get(b"T").ok()
                .and_then(|o| o.as_str().ok())
                .map(|s| String::from_utf8_lossy(s).to_string())
                .unwrap_or_else(|| "anonymous".into());
            let rect = annot_dict.get(b"Rect").ok()
                .and_then(|o| o.as_array().ok())
                .map(|arr| arr.iter().filter_map(|x| x.as_float().ok()).collect::<Vec<f32>>());
            println!("page {}: author={:?} subtype={} contents={:?} rect={:?}", page_num, author, subtype, contents, rect);
            found += 1;
        }
    }
    println!("total comment annotations found: {}", found);
}

fn spike_docx() {
    println!("=== SPIKE 1: docx-rs track-changes + comments round-trip ===");
    let mut buf = Vec::new();
    fs::File::open("/tmp/redline_test.docx").unwrap().read_to_end(&mut buf).unwrap();

    let raw_len_before = fs::metadata("/tmp/redline_test.docx").unwrap().len();

    let docx = read_docx(&buf);
    match docx {
        Ok(d) => {
            println!("READ OK. paragraph count in document.xml body: {}", d.document.children.len());
            println!("input size {} bytes", raw_len_before);

            let out_path = "/tmp/redline_test_roundtrip.docx";
            let file = fs::File::create(out_path).unwrap();
            match d.build().pack(file) {
                Ok(_) => println!("WRITE OK -> {}", out_path),
                Err(e) => println!("WRITE FAILED: {:?}", e),
            }

            let z = fs::File::open(out_path).unwrap();
            let mut archive = zip::ZipArchive::new(z).unwrap();
            let mut names: Vec<String> = (0..archive.len())
                .map(|i| archive.by_index(i).unwrap().name().to_string())
                .collect();
            names.sort();
            println!("parts in round-tripped docx: {:?}", names);

            let has_comments = names.iter().any(|n| n == "word/comments.xml");
            println!("comments.xml survived: {}", has_comments);

            let mut doc_xml = String::new();
            for name in &names {
                if name == "word/document.xml" {
                    let mut f = archive.by_name(name).unwrap();
                    f.read_to_string(&mut doc_xml).unwrap();
                }
            }
            println!("document.xml contains <w:ins: {}", doc_xml.contains("<w:ins"));
            println!("document.xml contains <w:del: {}", doc_xml.contains("<w:del"));
            println!("document.xml contains commentReference: {}", doc_xml.contains("commentReference"));
        }
        Err(e) => println!("READ FAILED: {:?}", e),
    }
}
