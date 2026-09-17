import { readFile } from 'node:fs/promises'
import { loadBindings, transform } from 'next/dist/build/swc/index.js'

export async function load(url, context, nextLoad) {
  if (!url.endsWith('.jsx')) return nextLoad(url, context)

  const source = await readFile(new URL(url), 'utf8')
  await loadBindings()
  const transformed = await transform(source, {
    filename: url,
    jsc: {
      parser: { syntax: 'ecmascript', jsx: true },
      transform: { react: { runtime: 'automatic' } },
    },
    module: { type: 'es6' },
  })
  return { format: 'module', source: transformed.code, shortCircuit: true }
}
