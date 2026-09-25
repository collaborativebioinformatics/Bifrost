const backendBase = process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_BASE_URL || 'http://127.0.0.1:8500'

async function forward(request, context) {
  const { path = [] } = await context.params
  const target = new URL(`/${path.join('/')}`, `${backendBase.replace(/\/$/, '')}/`)
  target.search = new URL(request.url).search

  const headers = new Headers()
  const contentType = request.headers.get('content-type')
  if (contentType) headers.set('content-type', contentType)

  const body = request.method === 'GET' || request.method === 'HEAD' ? undefined : await request.arrayBuffer()
  const response = await fetch(target, { method: request.method, headers, body, cache: 'no-store' })
  const responseHeaders = new Headers()
  const responseType = response.headers.get('content-type')
  if (responseType) responseHeaders.set('content-type', responseType)

  return new Response(response.body, { status: response.status, headers: responseHeaders })
}

export const GET = forward
export const POST = forward
export const OPTIONS = () => new Response(null, { status: 204 })
