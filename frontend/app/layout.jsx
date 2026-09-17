import './globals.css'

export const metadata = {
  title: 'Bifrost | Federated Analysis',
  description: 'Disclosure-safe cross-TRE analysis workspace',
}

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
