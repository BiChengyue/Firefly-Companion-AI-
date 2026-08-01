// url 模块 shim — pixi-live2d-display 需要 url.format/parse/resolve
// 直接实现而不 import 'url'（避免循环别名）

export function format(urlObj: any): string {
  const proto = urlObj.protocol || ''
  const auth = urlObj.auth || ''
  const host = urlObj.host || ''
  const pathname = urlObj.pathname || ''
  const search = urlObj.search || ''
  const hash = urlObj.hash || ''

  let result = ''
  if (proto) result += proto + '//'
  if (auth) result += auth + '@'
  result += host + pathname
  if (search) result += search
  if (hash) result += hash
  return result
}

export function parse(urlStr: string): any {
  const a = typeof document !== 'undefined'
    ? document.createElement('a')
    : { set href(_v: string) {}, protocol: '', host: '', pathname: '', search: '', hash: '', hostname: '', port: '' } as any
  if (typeof document !== 'undefined') a.href = urlStr
  return {
    protocol: a.protocol,
    host: a.host,
    hostname: a.hostname,
    port: a.port,
    pathname: a.pathname,
    search: a.search,
    hash: a.hash,
    href: urlStr,
  }
}

export function resolve(from: string, to: string): string {
  return new URL(to, from).href
}

export default { format, parse, resolve }
