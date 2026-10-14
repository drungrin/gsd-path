#!/usr/bin/env node
// Preview by default. --post sends one bounded release embed to Discord.
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

function parseArgs(args) {
  const options = { post: false };
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--post') options.post = true;
    else if (arg === '--latest') options.latest = true;
    else if (arg === '--tag' || arg === '--repo') {
      const value = args[++i];
      if (!value || value.startsWith('--')) throw new Error(`Missing value for ${arg}`);
      options[arg.slice(2)] = value;
    } else throw new Error(`Unknown argument: ${arg}`);
  }
  if (!options.repo || !/^[\w.-]+\/[\w.-]+$/.test(options.repo)) {
    throw new Error('--repo must be owner/repository');
  }
  if (Boolean(options.tag) === Boolean(options.latest)) {
    throw new Error('Choose exactly one of --tag or --latest');
  }
  return options;
}

// Summary formatting adapted from open-gsd/gsd-core (MIT).
function classifyTitle(line) {
  const title = line.replace(/^[*-]\s+/, '');
  if (/^feat(?:ure)?(?:\([^)]*\))?!?:/i.test(title)) return 'Feature';
  if (/^fix(?:\([^)]*\))?!?:/i.test(title)) return 'Fix';
  if (/^(?:docs|refactor|test|ci|chore|perf|revert)(?:\([^)]*\))?!?:/i.test(title)) return 'Internal';
  return 'Enhancement';
}

const DEFAULT_MAX_CONTENT = 1850;
const SECTION_LIMITS = Object.freeze([
  ['Breaking', 4],
  ['Security', 4],
  ['Feature', 4],
  ['Fix', 4],
  ['Enhancement', 3],
  ['Internal', 2],
  ['New Contributors', 3],
]);

function normalizeNewlines(value) {
  return String(value || '').replace(/\r\n/g, '\n');
}

function releaseTag(release) {
  return release.tagName || release.tag_name || release.name || 'release';
}

function releaseName(release) {
  return release.name || releaseTag(release);
}

function releaseUrl(release) {
  return release.html_url || release.url || '';
}

function releaseIsPrerelease(release) {
  return release.isPrerelease === true || release.prerelease === true || /-/.test(releaseTag(release));
}

function loadPackageName(repoRoot = path.resolve(__dirname, '..', '..')) {
  const pkgJson = JSON.parse(fs.readFileSync(path.join(repoRoot, 'package.json'), 'utf8'));
  return pkgJson.name || 'package';
}

function normalizeHeading(value) {
  const heading = value.trim().replace(/:$/, '');
  const lower = heading.toLowerCase();

  if (lower.includes('breaking')) return 'Breaking';
  if (lower.includes('security')) return 'Security';
  if (lower === 'added' || lower === 'feature' || lower === 'features') return 'Feature';
  if (lower === 'fixed' || lower === 'fix' || lower === 'fixes' || lower === 'bug fixes') return 'Fix';
  if (lower === 'changed' || lower === 'enhancement' || lower === 'enhancements' || lower === 'improvements') return 'Enhancement';
  if (lower === 'internal' || lower === 'maintenance') return 'Internal';
  if (lower === 'new contributors') return 'New Contributors';
  if (lower === "what's changed" || lower === 'whats changed') return "What's Changed";
  return heading;
}

function collectSections(body) {
  const sections = new Map();
  let current = null;

  for (const line of normalizeNewlines(body).split('\n')) {
    const trimmed = line.trim();
    const heading = trimmed.match(/^#{2,3}\s+(.+)$/);

    if (heading) {
      current = normalizeHeading(heading[1]);
      continue;
    }

    const bullet = trimmed.match(/^([*-])\s+(.+)$/);
    if (!bullet || !current) continue;

    let section = current;
    if (section === "What's Changed") {
      section = classifyTitle(trimmed);
    }

    if (!sections.has(section)) sections.set(section, []);
    sections.get(section).push(cleanBullet(trimmed));
  }

  return sections;
}

function cleanBullet(value) {
  let text = String(value)
    .replace(/^[*-]\s+/, '')
    .replace(/\s+by\s+@[A-Za-z0-9-]+\s+in\s+https:\/\/github\.com\/[^/\s]+\/[^/\s]+\/pull\/(\d+)/g, ' (#$1)')
    .replace(/\[([^\]]+)\]\((https:\/\/github\.com\/[^/\s]+\/[^/\s]+\/(?:pull|issues)\/(\d+))\)/g, '$1 (#$3)')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '$1')
    .replace(/https:\/\/github\.com\/[^/\s]+\/[^/\s]+\/pull\/(\d+)/g, '#$1')
    .replace(/https:\/\/github\.com\/[^/\s]+\/[^/\s]+\/issues\/(\d+)/g, '#$1')
    .replace(/https:\/\/github\.com\/[^/\s]+\/[^/\s]+\/commit\/([0-9a-f]{7})[0-9a-f]*/gi, '$1')
    .replace(/\*\*/g, '')
    .replace(/\s+/g, ' ')
    .trim();

  text = text
    .replace(/^\[codex\]\s+/i, '')
    .replace(/^(?:feat|fix|docs|chore|ci|refactor|test)(?:\([^)]+\))?!?:\s+/i, '');
  text = text.replace(/#(\d+)\s+\(#\1\)/g, '#$1');
  text = text.replace(/\s+\(#(\d+)\)\s+\(#\1\)$/g, ' (#$1)');

  return truncateAtWord(text, 220);
}

function truncateAtWord(value, maxLength) {
  if (value.length <= maxLength) return value;
  const splitAt = value.lastIndexOf(' ', maxLength - 3);
  if (splitAt < 80) return `${value.slice(0, maxLength - 3)}...`;
  return `${value.slice(0, splitAt)}...`;
}

function extractInstallCommand(body, packageName, release) {
  const lines = normalizeNewlines(body).split('\n');
  let inInstall = false;
  let inFence = false;

  for (const line of lines) {
    const trimmed = line.trim();

    if (/^##\s+Install\b/i.test(trimmed)) {
      inInstall = true;
      continue;
    }

    if (inInstall && /^##\s+/.test(trimmed)) break;
    if (!inInstall) continue;

    if (trimmed.startsWith('```')) {
      inFence = !inFence;
      continue;
    }

    if (inFence && /^npm\s+(?:i|install)\s+/.test(trimmed)) {
      return trimmed;
    }
  }

  const channel = releaseIsPrerelease(release) ? 'next' : 'latest';
  return `npm i ${packageName}@${channel}`;
}

function buildDiscordReleasePayload({ release, packageName = loadPackageName(), maxContent = DEFAULT_MAX_CONTENT }) {
  const tag = releaseTag(release);
  const name = releaseName(release);
  const url = releaseUrl(release);
  const prerelease = releaseIsPrerelease(release);
  const channel = prerelease ? 'next' : 'latest';
  const body = release.body || '';
  const sections = collectSections(body);
  const footer = url ? `Full changelog: ${url}` : `Full changelog: ${tag}`;
  const baseLines = [
    `**${packageName} ${name}${prerelease ? ' pre-release' : ''} is out**`,
    '',
    'Install:',
    `\`${extractInstallCommand(body, packageName, release)}\``,
  ];

  const lines = baseLines.slice();
  for (const [section, limit] of SECTION_LIMITS) {
    appendSection(lines, section, sections.get(section) || [], limit, footer, maxContent);
  }

  if (lines.length === baseLines.length) {
    appendLineGroup(lines, ['', 'No release notes were provided.'], footer, maxContent);
  }

  if (joinedLength(lines, footer) > maxContent) {
    while (lines.length > baseLines.length && joinedLength(lines, footer) > maxContent) {
      lines.pop();
    }
  }

  const content = [...lines, '', footer].join('\n');
  return {
    username: 'GSD Releases',
    content,
    embeds: [
      {
        title: 'Release details',
        url: url || undefined,
        color: prerelease ? 0xd9a441 : 0x2f9e44,
        fields: [
          { name: 'Package', value: `\`${packageName}\``, inline: true },
          { name: 'Channel', value: `\`${channel}\``, inline: true },
          { name: 'Version', value: `\`${tag}\``, inline: true },
        ],
      },
    ],
    allowed_mentions: { parse: [] },
  };
}

function appendSection(lines, section, bullets, limit, footer, maxContent) {
  if (bullets.length === 0) return;

  const label = sectionLabel(section);
  const selected = [];
  for (const bullet of bullets.slice(0, limit)) {
    const candidate = ['', `**${label}**`, ...selected.map((item) => `- ${item}`), `- ${bullet}`];
    if (joinedLength([...lines, ...candidate], footer) > maxContent) break;
    selected.push(bullet);
  }

  if (selected.length === 0) return;

  const group = ['', `**${label}**`, ...selected.map((item) => `- ${item}`)];
  const remaining = bullets.length - selected.length;
  if (remaining > 0) {
    const moreLine = `- ...and ${remaining} more ${label.toLowerCase()}`;
    if (joinedLength([...lines, ...group, moreLine], footer) <= maxContent) {
      group.push(moreLine);
    }
  }

  appendLineGroup(lines, group, footer, maxContent);
}

function appendLineGroup(lines, group, footer, maxContent) {
  if (joinedLength([...lines, ...group], footer) <= maxContent) {
    lines.push(...group);
  }
}

function joinedLength(lines, footer) {
  return [...lines, '', footer].join('\n').length;
}

function sectionLabel(section) {
  if (section === 'Feature') return 'Features';
  if (section === 'Fix') return 'Fixes';
  if (section === 'Enhancement') return 'Enhancements';
  return section;
}

function buildPayload(release, repo) {
  if (release.draft) throw new Error('Refusing to announce a draft release');
  if (!release.html_url || !release.tag_name) throw new Error('Release is missing its URL or tag');
  return buildDiscordReleasePayload({ release });
}

async function postPayload(payload, webhook, fetchImpl = fetch) {
  if (!webhook) throw new Error('Set the DISCORD_CHANGELOG_WEBHOOK repository secret');
  let url;
  try { url = new URL(webhook); } catch { throw new Error('Invalid Discord webhook URL'); }
  if (url.protocol !== 'https:' || !['discord.com', 'discordapp.com'].includes(url.hostname)
      || [url.username, url.password, url.port].some(Boolean)
      || !/^\/api(?:\/v\d+)?\/webhooks\/\d+\/[^/]+$/.test(url.pathname)) {
    throw new Error('Invalid Discord webhook URL');
  }
  // wait=true makes Discord confirm creation and return a message ID.
  url.searchParams.set('wait', 'true');
  let response;
  try {
    response = await fetchImpl(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload), redirect: 'error', signal: AbortSignal.timeout(30000),
    });
  } catch { throw new Error('Discord request failed or timed out; check delivery before retrying'); }
  if (!response.ok) throw new Error(`Discord rejected the webhook (HTTP ${response.status})`);
  const message = await response.json();
  if (!message.id) throw new Error('Discord did not confirm a message ID');
  return message.id;
}

async function main(args) {
  const options = parseArgs(args);
  const endpoint = options.latest ? 'latest' : `tags/${encodeURIComponent(options.tag)}`;
  const release = JSON.parse(execFileSync('gh', ['api', `repos/${options.repo}/releases/${endpoint}`], {
    encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'], maxBuffer: 10 * 1024 * 1024,
  }));
  const payload = buildPayload(release, options.repo);
  if (!options.post) return console.log(JSON.stringify(payload, null, 2));
  const id = await postPayload(payload, process.env.DISCORD_WEBHOOK_URL);
  console.log(`Posted ${release.tag_name} to Discord; message ID: ${id}`);
}

module.exports = { parseArgs, buildPayload, postPayload, buildDiscordReleasePayload, cleanBullet, collectSections, extractInstallCommand };
if (require.main === module) main(process.argv.slice(2)).catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
