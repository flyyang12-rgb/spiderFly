export const API = '/api'

export async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) }
  if (options.body && !(options.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }
  const response = await fetch(API + path, { credentials: 'include', ...options, headers })
  if (!response.ok) {
    let message = '请求失败（' + response.status + '）'
    try {
      const data = await response.json()
      if (typeof data.detail === 'string') message = data.detail
      else if (Array.isArray(data.detail))
        message =
          data.detail
            .map((item) => item.msg)
            .filter(Boolean)
            .join('；') || message
    } catch {}
    const error = new Error(message)
    error.status = response.status
    throw error
  }
  if (response.status === 204) return null
  return response.json()
}
