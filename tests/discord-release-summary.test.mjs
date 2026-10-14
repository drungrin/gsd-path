import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
'use strict';

const assert = require('node:assert/strict');
const { describe, test } = require('node:test');
const { readFileSync } = require('node:fs');
const { parse: parseYaml } = require('yaml');
const {
  buildDiscordReleasePayload,
  cleanBullet,
  collectSections,
  extractInstallCommand,
  buildPayload,
  postPayload,
  parseArgs,
} = require('../scripts/release-notes/discord-release-summary.cjs');

const sampleRelease = {
  tagName: 'v1.4.0-rc.1',
  name: 'v1.4.0-rc.1',
  isPrerelease: true,
  url: 'https://github.com/open-gsd/gsd-core/releases/tag/v1.4.0-rc.1',
  body: [
    '## Install',
    '',
    'This pre-release is published to npm under the `next` dist-tag.',
    '',
    '```bash',
    'npm i @opengsd/gsd-core@1.4.0-rc.1',
    '# or',
    'npm i @opengsd/gsd-core@next',
    '```',
    '',
    '## What\'s Changed',
    '',
    '### Feature',
    '* feat(#656): Research module cache by @trek-e in https://github.com/open-gsd/gsd-core/pull/664',
    '* feat(#717): size budget on bytes by @trek-e in https://github.com/open-gsd/gsd-core/pull/719',
    '* feat(#159): auto-use existing RESEARCH.md by @trek-e in https://github.com/open-gsd/gsd-core/pull/718',
    '* feat(#703): add --granularity override by @trek-e in https://github.com/open-gsd/gsd-core/pull/750',
    '* feat(#25): scope verifier Step 7b by @trek-e in https://github.com/open-gsd/gsd-core/pull/753',
    '',
    '### Fix',
    '* fix(#663): resolve CodeQL alerts by @trek-e in https://github.com/open-gsd/gsd-core/pull/665',
    '',
    '**Full Changelog**: https://github.com/open-gsd/gsd-core/compare/v1.3.1...v1.4.0-rc.1',
  ].join('\n'),
};

describe('discord release summary', () => {
  test('builds a hybrid payload with a preserved full changelog URL', () => {
    const payload = buildDiscordReleasePayload({
      release: sampleRelease,
      packageName: '@opengsd/gsd-core',
      maxContent: 1850,
    });

    assert.equal(payload.username, 'GSD Releases');
    assert.match(payload.content, /\*\*@opengsd\/gsd-core v1\.4\.0-rc\.1 pre-release is out\*\*/);
    assert.match(payload.content, /`npm i @opengsd\/gsd-core@1\.4\.0-rc\.1`/);
    assert.match(payload.content, /Full changelog: https:\/\/github\.com\/open-gsd\/gsd-core\/releases\/tag\/v1\.4\.0-rc\.1/);
    assert.doesNotMatch(payload.content, /\*\*Full Changelog\*\*:\s*$/m);
    assert.match(payload.content, /Research module cache \(#664\)/);
    assert.match(payload.content, /\.\.\.and 1 more features/);
    assert.equal(payload.embeds[0].fields[1].value, '`next`');
  });

  test('summarizes auto-generated What Changed bullets when release notes are not yet curated', () => {
    const sections = collectSections([
      '## What\'s Changed',
      '* feat: add thing by @trek-e in https://github.com/open-gsd/gsd-core/pull/10',
      '* fix: repair thing by @trek-e in https://github.com/open-gsd/gsd-core/pull/11',
      '* enhance: improve thing by @trek-e in https://github.com/open-gsd/gsd-core/pull/13',
      '* docs: explain thing by @trek-e in https://github.com/open-gsd/gsd-core/pull/12',
    ].join('\n'));

    assert.deepEqual(sections.get('Feature'), ['add thing (#10)']);
    assert.deepEqual(sections.get('Fix'), ['repair thing (#11)']);
    // enhance: is a genuine user-facing enhancement (not in the non-user-facing set).
    assert.ok(sections.get('Enhancement') && sections.get('Enhancement').length === 1, 'enhance: bullet lands in Enhancement');
    // #2716: docs: routes to the Internal section, not Enhancement.
    assert.deepEqual(sections.get('Internal'), ['explain thing (#12)']);
  });

  test('cleans common GitHub release-note link noise without deleting issue references', () => {
    assert.equal(
      cleanBullet('* fix: repair [#670](https://github.com/open-gsd/gsd-core/issues/670) by @trek-e in https://github.com/open-gsd/gsd-core/pull/675'),
      'repair #670 (#675)'
    );
  });

  test('falls back to the release channel when no install block exists', () => {
    assert.equal(
      extractInstallCommand('', '@opengsd/gsd-core', { tagName: 'v1.4.0-rc.1', isPrerelease: true }),
      'npm i @opengsd/gsd-core@next'
    );
    assert.equal(
      extractInstallCommand('', '@opengsd/gsd-core', { tagName: 'v1.4.0', isPrerelease: false }),
      'npm i @opengsd/gsd-core@latest'
    );
  });
});

test('path release headings produce a bounded summary with fixes and install instructions', () => {
  const release = {
    tag_name: 'v1.6.0', name: 'gsd-path 1.6.0',
    html_url: 'https://github.com/open-gsd/gsd-path/releases/tag/v1.6.0',
    url: 'https://api.github.com/repos/open-gsd/gsd-path/releases/123',
    body: ['### Added', ...Array(30).fill('- New feature '.repeat(30)),
      '### Changed', '- Improve behavior', '### Fixed', '- Repair release trigger'].join('\n'),
  };
  const payload = buildPayload(release);
  assert.ok(payload.content.length <= 1850);
  assert.match(payload.content, /npm i @opengsd\/gsd-path@latest/);
  assert.match(payload.content, /\*\*Features\*\*/);
  assert.match(payload.content, /Repair release trigger/);
  assert.match(payload.content, /Full changelog: https:\/\/github.com\/open-gsd\/gsd-path/);
  assert.deepEqual(payload.allowed_mentions, { parse: [] });
  assert.throws(() => buildPayload({ ...release, draft: true }), /draft/);
});

test('posting waits for a confirmed Discord message and preserves thread targeting', async () => {
  const payload = { content: 'test', allowed_mentions: { parse: [] } };
  const id = await postPayload(payload, 'https://discord.com/api/webhooks/123/token?thread_id=456', async (url, options) => {
    assert.equal(url.searchParams.get('wait'), 'true');
    assert.equal(url.searchParams.get('thread_id'), '456');
    assert.equal(options.redirect, 'error');
    assert.equal(options.method, 'POST');
    assert.deepEqual(JSON.parse(options.body), payload);
    return { ok: true, json: async () => ({ id: '789' }) };
  });
  assert.equal(id, '789');
});

test('missing secrets, invalid URLs, rejected posts and unconfirmed delivery fail clearly', async () => {
  await assert.rejects(postPayload({}, ''), /repository secret/);
  await assert.rejects(postPayload({}, 'https://example.com/api/webhooks/123/token'), /Invalid/);
  const url = 'https://discord.com/api/webhooks/123/secret-token';
  await assert.rejects(postPayload({}, url, async () => ({ ok: false, status: 401 })), /HTTP 401/);
  await assert.rejects(postPayload({}, url, async () => ({ ok: true, json: async () => ({}) })), /message ID/);
  await assert.rejects(postPayload({}, url, async () => { throw new Error(url); }), (error) => {
    assert.match(error.message, /timed out/);
    assert.ok(!error.message.includes('secret-token'));
    return true;
  });
});

test('CLI preview is the default and release selection is explicit', () => {
  assert.equal(parseArgs(['--repo', 'open-gsd/gsd-path', '--latest']).post, false);
  assert.equal(parseArgs(['--repo', 'open-gsd/gsd-path', '--tag', 'v1.6.0', '--post']).tag, 'v1.6.0');
  assert.throws(() => parseArgs(['--repo', 'open-gsd/gsd-path', '--tag']), /Missing value/);
  assert.throws(() => parseArgs(['--repo', 'open-gsd/gsd-path', '--latest', '--tag', 'v1.6.0']), /exactly one/);
});

test('automated release invokes the announcement after publishing with its exact tag and secret', () => {
  const load = (name) => parseYaml(readFileSync(new URL(`../.github/workflows/${name}`, import.meta.url), 'utf8'));
  const release = load('release.yml');
  const discord = load('release-discord-changelog.yml');
  const job = release.jobs['discord-changelog'];
  assert.equal(job.needs, 'publish');
  assert.equal(job.uses, './.github/workflows/release-discord-changelog.yml');
  assert.equal(job.with.release_tag, '${{ needs.publish.outputs.release_tag }}');
  assert.equal(release.jobs.publish.outputs.release_tag, '${{ steps.version.outputs.tag }}');
  assert.equal(job.secrets.DISCORD_CHANGELOG_WEBHOOK, '${{ secrets.DISCORD_CHANGELOG_WEBHOOK }}');
  assert.ok(discord.on.workflow_call.secrets.DISCORD_CHANGELOG_WEBHOOK.required);
  assert.ok(discord.on.workflow_dispatch.inputs.release_tag);
  assert.deepEqual(discord.on.release.types, ['published']);
  assert.equal(discord.jobs['discord-changelog'].if, undefined);
});
