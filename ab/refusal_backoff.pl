undef $/;
my $source = <STDIN>;
sub once {
    my ($pattern, $replacement, $name) = @_;
    my $count = () = $source =~ /$pattern/g;
    die "$name matched $count times" unless $count == 1;
    $source =~ s/$pattern/$replacement/;
}
once(
    qr/\n    let stamp = process_start_stamp\(pid\);\n/,
    "\n    if REFUSED_AT\n        .lock()\n        .ok()\n        .and_then(|refused| refused.get(&pid).copied())\n        .is_some_and(|at| at.elapsed() < Duration::from_secs(3))\n    {\n        return;\n    }\n    let stamp = process_start_stamp(pid);\n",
    "skip"
);
once(
    qr/\n    let opt_in = enable_chromium_accessibility\(app_element\);\n/,
    "\n    let opt_in = enable_chromium_accessibility(app_element);\n    if opt_in == AccessibilityOptIn::NotAccepted {\n        if let Ok(mut refused) = REFUSED_AT.lock() {\n            refused.insert(pid, Instant::now());\n        }\n    }\n",
    "record"
);
once(
    qr/\nconst CHROMIUM_SETTLE_MAX_SECONDS/,
    "\nstatic REFUSED_AT: LazyLock<Mutex<HashMap<i32, Instant>>> =\n    LazyLock::new(|| Mutex::new(HashMap::new()));\n\nconst CHROMIUM_SETTLE_MAX_SECONDS",
    "static"
);
print $source;
