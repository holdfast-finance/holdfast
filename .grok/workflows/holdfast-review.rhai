let meta = #{
    name: "holdfast-review",
    description: "Run Holdfast's read-only review lanes and write a packet for Keel.",
    when_to_use: "Before handing a branch to Keel. Slash command /holdfast-review. Optional args.base (default origin/main).",
    phases: [
        #{ title: "Review", detail: "Bulwark, Ledger, Prism, and Sounding" },
        #{ title: "Packet", detail: "One gate packet, no merge" },
    ],
};

let base = "origin/main";
if args != () {
    if args.base != () {
        base = args.base;
    }
}

phase("Review");
let jobs = [];
jobs.push(#{
    prompt: "You are Bulwark. Review the diff from " + base + " to HEAD. Run git rev-parse HEAD and git diff " + base + "...HEAD. Read CLAUDE.md and .claude/rules/security.md. Return ranked findings with file:line, or say no findings. Name the head SHA. Do not edit files and do not merge. Sign the verdict \"— Bulwark via Helm (advisory)\".",
    label: "bulwark",
    agent_type: "security-reviewer",
    capability_mode: "execute",
});
jobs.push(#{
    prompt: "You are Ledger in review mode. Review the diff from " + base + " to HEAD for money invariants. Run git rev-parse HEAD and git diff " + base + "...HEAD. Read .claude/rules/money.md. Return findings with file:line, or say no findings. Name the head SHA. Do not edit files. Sign the verdict \"— Ledger via Helm (advisory)\".",
    label: "ledger",
    agent_type: "money-invariants-reviewer",
    capability_mode: "execute",
});
jobs.push(#{
    prompt: "You are Prism in review mode. Review web and site changes from " + base + " to HEAD. Run git diff " + base + "...HEAD -- web site. Read docs/brand/BRAND.md and .claude/rules/frontend.md. Return blocking and polish findings with file:line. Name the head SHA. Do not edit source files. Sign the verdict \"— Prism via Helm (advisory)\".",
    label: "prism",
    agent_type: "ui-brand-reviewer",
    capability_mode: "execute",
});
jobs.push(#{
    prompt: "You are Sounding. Read docs/STATUS.md and name the active phase. Run git rev-parse HEAD. Report which standing gates you could not run in this pass and why. Do not edit product code. Do not claim PASS without command output. Sign the verdict \"— Sounding via Helm (advisory)\".",
    label: "sounding",
    agent_type: "phase-acceptance-checker",
    capability_mode: "execute",
});
let results = parallel(jobs);

phase("Packet");
let labels = ["Bulwark", "Ledger", "Prism", "Sounding"];
let report = "# Holdfast review packet\n\nBase: " + base + "\n\n";
let i = 0;
for r in results {
    report += "## " + labels[i] + "\n\n";
    if r != () && r.success {
        let text = r.output;
        if type_of(text) != "string" {
            text = json_encode(text);
        }
        report += text;
    } else {
        report += "NO VERDICT. Treat this lane as failed closed.\n";
    }
    report += "\n\n";
    i += 1;
}
report += "These reviews are advisory (— <Role> via Helm). Protected paths still need a Claude-team Bulwark verdict.\n";
report += "Helm does not merge. Hand this packet and the head SHA to Claude's Keel.\n";
let path = write_scratch_file("holdfast-review.md", report);
log("review packet written");
complete(#{ summary: "Review packet ready for Keel. No merge was performed.", path: path });
