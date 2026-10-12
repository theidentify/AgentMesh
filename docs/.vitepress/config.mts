import { defineConfig } from 'vitepress'

const repo = 'https://github.com/theidentify/AgentMesh'

export default defineConfig({
  title: 'AgentMesh',
  description: 'Local-first memory sync for AI agents',
  base: '/AgentMesh/',
  cleanUrls: true,
  lastUpdated: true,
  srcExclude: ['superpowers/**', 'scripts/**', 'README.md'],
  head: [['link', { rel: 'icon', type: 'image/svg+xml', href: '/AgentMesh/agentmesh-icon.svg' }]],
  themeConfig: {
    logo: '/agentmesh-icon.svg',
    nav: [
      { text: 'Guide', link: '/getting-started/concepts' },
      { text: 'Reference', link: '/reference/' },
    ],
    sidebar: [
      { text: 'Getting started', items: [
        { text: 'Overview', link: '/' },
        { text: 'Concepts', link: '/getting-started/concepts' },
        { text: 'Prerequisites', link: '/getting-started/prerequisites' },
      ] },
      { text: 'Install', items: [
        { text: 'macOS', link: '/install/macos' },
        { text: 'Linux', link: '/install/linux' },
        { text: 'Windows', link: '/install/windows' },
        { text: 'Run in the background', link: '/install/background-service' },
      ] },
      { text: 'Use', items: [
        { text: 'Recall memory', link: '/use/recall' },
        { text: 'Ingest agent sessions', link: '/use/ingest' },
        { text: 'Check sync status', link: '/use/status' },
        { text: 'Insight dashboard', link: '/use/insight' },
      ] },
      { text: 'Help', items: [
        { text: 'Troubleshooting', link: '/troubleshooting' },
        { text: 'Privacy', link: '/privacy' },
        { text: 'Reference', link: '/reference/' },
      ] },
    ],
    search: { provider: 'local' },
    socialLinks: [{ icon: 'github', link: repo }],
    editLink: { pattern: `${repo}/edit/main/docs/:path`, text: 'Edit this page on GitHub' },
    footer: { message: 'Code, tests and documentation only. Real memories never belong in this repository.' },
  },
})
