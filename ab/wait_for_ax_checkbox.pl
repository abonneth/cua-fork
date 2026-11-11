undef $/;
my $s = <STDIN>;
my $anchor = "            Ok(None) => match exact_pixel_setup_checkbox(\n";
my $arm = <<'RUST';
            Ok(None)
                if super::platform::guarded_attach_active()
                    && !tree.nodes.iter().any(|node| {
                        node.role == "AXCheckBox"
                            && node.title.as_deref() == Some(descriptor.checkbox_label)
                    }) =>
            {
                release_actionable_nodes(&tree.nodes)
            }
RUST
my $count = () = $s =~ /\Q$anchor\E/g;
die "anchor count $count" unless $count == 1;
$s =~ s/\Q$anchor\E/$arm$anchor/;
print $s;
