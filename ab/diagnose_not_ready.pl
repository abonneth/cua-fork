undef $/;
my $source = <STDIN>;
my $anchor = qr/(        if Instant::now\(\) >= deadline \{\n)(            return Err\(handle\.abort\(\n                pid,\n                window_id,\n                BrowserRefusal::new\(\n                    BrowserRefusalCode::BrowserWrongTargetRefused,\n                    format!\(\n                        "the exact \{\} remote-debugging setup page did not become ready)(",\n                        descriptor\.product_name)(\n)/;
my $diagnosis = <<'RUST';
            let describe = |scope: &str, tree: &TreeWalkResult| {
                let sample: Vec<String> = tree
                    .nodes
                    .iter()
                    .filter(|node| {
                        node.in_web_content
                            || matches!(
                                node.role.as_str(),
                                "AXWebArea" | "AXCheckBox" | "AXTextField" | "AXRadioButton"
                            )
                    })
                    .take(40)
                    .map(|node| {
                        let label: String = node
                            .title
                            .as_deref()
                            .or(node.value.as_deref())
                            .or(node.description.as_deref())
                            .unwrap_or("")
                            .chars()
                            .take(30)
                            .collect();
                        format!("{}:{}", node.role, label)
                    })
                    .collect();
                format!(
                    "{scope}: nodes={} web={} truncated={} depth_truncated={} sample={:?}",
                    tree.nodes.len(),
                    tree.nodes.iter().filter(|node| node.in_web_content).count(),
                    tree.truncated,
                    tree.depth_truncated,
                    sample
                )
            };
            let window_tree = walk_tree(pid, Some(window_id), None);
            let app_tree = walk_tree(pid, None, None);
            let diagnosis = format!(
                "{} | {}",
                describe("window", &window_tree),
                describe("app", &app_tree)
            );
            release_actionable_nodes(&window_tree.nodes);
            release_actionable_nodes(&app_tree.nodes);
RUST
my $count = ($source =~ s/$anchor/$1$diagnosis$2 [{}]$3,\n                        diagnosis$4/);
die "anchor matched $count times" unless $count == 1;
print $source;
