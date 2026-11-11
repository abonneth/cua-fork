undef $/;
my $kind = shift;
my $source = <STDIN>;
my $helper = <<'RUST';

pub(crate) fn ab_trace(line: String) {
    use std::io::Write;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs_f64())
        .unwrap_or(0.0);
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open("/tmp/cua-ab-trace.log")
    {
        let _ = writeln!(file, "{now:.3} {line}");
    }
}
RUST
sub once {
    my ($pattern, $replacement, $name) = @_;
    my $count = () = $source =~ /$pattern/g;
    die "$name matched $count times" unless $count == 1;
    $source =~ s/$pattern/$replacement/;
}
if ($kind eq "enablement") {
    once(
        qr/\n        Attempt::Run \{ prior_timeouts \} => prior_timeouts,\n    \};\n    let outcome = wait_outcome\(\n        enable_chromium_accessibility\(app_element\),\n/,
        "\n        Attempt::Run { prior_timeouts } => prior_timeouts,\n    };\n    let opt_in = enable_chromium_accessibility(app_element);\n    ab_trace(format!(\"enable pid={pid} prior_timeouts={prior_timeouts} opt_in={opt_in:?}\"));\n    let outcome = wait_outcome(\n        opt_in,\n",
        "enable"
    );
    once(
        qr/\n    if let \(Some\(stamp\), Some\(wait\)\) = \(stamp, outcome\) \{\n/,
        "\n    ab_trace(format!(\n        \"enable pid={pid} outcome={outcome:?} waited_ms={}\",\n        attempted_at.elapsed().as_millis()\n    ));\n    if let (Some(stamp), Some(wait)) = (stamp, outcome) {\n",
        "outcome"
    );
    once(qr/\nconst CHROMIUM_SETTLE_MAX_SECONDS/, "$helper\nconst CHROMIUM_SETTLE_MAX_SECONDS", "helper");
} elsif ($kind eq "setup") {
    once(
        qr/\n    let deadline = Instant::now\(\) \+ EXISTING_PROFILE_SETUP_READY_TIMEOUT;\n    loop \{\n/,
        "\n    ab_trace(format!(\"setup-wait start pid={pid}\"));\n    let deadline = Instant::now() + EXISTING_PROFILE_SETUP_READY_TIMEOUT;\n    loop {\n",
        "wait"
    );
    once(
        qr/\n        let checkbox = exact_setup_checkbox\(&tree, descriptor\);\n        match checkbox \{\n/,
        "\n        ab_trace(format!(\n            \"setup-loop nodes={} web={} checkbox={} guarded={}\",\n            tree.nodes.len(),\n            tree.nodes.iter().filter(|node| node.in_web_content).count(),\n            tree.nodes.iter().any(|node| node.role == \"AXCheckBox\"),\n            super::platform::guarded_attach_active()\n        ));\n        let checkbox = exact_setup_checkbox(&tree, descriptor);\n        match checkbox {\n",
        "loop"
    );
    once(qr/\nfn field_equals\(/, "$helper\nfn field_equals(", "helper");
} elsif ($kind eq "bindings" || $kind eq "bindings-fallback") {
    once(
        qr/\n    let manual = set_bool_attr_true\(app_element, "AXManualAccessibility"\);\n/,
        "\n    let manual = set_bool_attr_true(app_element, \"AXManualAccessibility\");\n    super::enablement::ab_trace(format!(\"manual={manual}\"));\n",
        "manual"
    );
    once(
        qr/\n    if set_bool_attr_true\(app_element, "AXEnhancedUserInterface"\) == kAXErrorSuccess \{\n/,
        "\n    let enhanced = set_bool_attr_true(app_element, \"AXEnhancedUserInterface\");\n    super::enablement::ab_trace(format!(\"enhanced={enhanced}\"));\n    if enhanced == kAXErrorSuccess {\n",
        "enhanced"
    );
    if ($kind eq "bindings-fallback") {
        once(
            qr/\n    if manual != kAXErrorAttributeUnsupported \{\n(?:        \/\/[^\n]*\n)*        return AccessibilityOptIn::NotAccepted;\n    \}\n/,
            "\n    let _ = kAXErrorAttributeUnsupported;\n",
            "fallback"
        );
    }
} else {
    die "unknown kind $kind";
}
print $source;
