import { Marked } from 'marked'
import createDOMPurify from 'dompurify'

const escape = text => String(text).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;')
const markdown = new Marked({ gfm: true, breaks: true, renderer: {
  html: ({ text }) => escape(text),
  // Messages never load remote images or active embeds automatically.
  image: ({ text }) => escape(text),
} })
const purifier = createDOMPurify(window)
purifier.addHook('afterSanitizeAttributes', node => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

export function renderAiMarkdown(content) {
  return purifier.sanitize(markdown.parse(String(content ?? '')), {
    ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'del', 'ul', 'ol', 'li', 'blockquote', 'pre', 'code',
      'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'a'],
    ALLOWED_ATTR: ['href', 'title', 'start', 'target', 'rel'],
    ALLOW_DATA_ATTR: false,
    SANITIZE_NAMED_PROPS: true,
  })
}
