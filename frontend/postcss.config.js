import { createRequire } from 'module'

const require = createRequire(import.meta.url)

function optionalPlugin(name) {
  try {
    return require(name)
  } catch {
    return null
  }
}

const tailwindcss = optionalPlugin('tailwindcss')
const autoprefixer = optionalPlugin('autoprefixer')

export default {
  plugins: [
    tailwindcss ? tailwindcss() : null,
    autoprefixer ? autoprefixer() : null,
  ].filter(Boolean),
}
