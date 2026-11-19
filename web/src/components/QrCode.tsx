import { useMemo } from 'react'
import { encode } from 'uqr'

/**
 * QR code rendered locally as SVG (uqr, MIT, zero deps). The otpauth:// URI holds the TOTP
 * secret, so it must never be sent to a third-party QR service.
 *
 * Dark modules on a white quiet zone regardless of theme: authenticator cameras expect
 * dark-on-light. Both colours are tokens (`--qr-ink`, `--qr-paper`).
 */
export function QrCode({
  value,
  label,
  size = 208,
}: {
  value: string
  label: string
  size?: number
}) {
  const path = useMemo(() => {
    const { data } = encode(value, { ecc: 'M', border: 0 })
    const parts: string[] = []
    data.forEach((row, y) => {
      row.forEach((on, x) => {
        if (on) parts.push(`M${String(x)} ${String(y)}h1v1h-1z`)
      })
    })
    return { d: parts.join(''), modules: data.length }
  }, [value])

  const quiet = 4
  const box = path.modules + quiet * 2
  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`${String(-quiet)} ${String(-quiet)} ${String(box)} ${String(box)}`}
      width={size}
      height={size}
      shapeRendering="crispEdges"
      className="rounded-lg"
    >
      <rect x={-quiet} y={-quiet} width={box} height={box} style={{ fill: 'var(--qr-paper)' }} />
      <path d={path.d} style={{ fill: 'var(--qr-ink)' }} />
    </svg>
  )
}
