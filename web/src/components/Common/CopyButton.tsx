import React, { useState } from 'react'
import { Copy, Check } from 'lucide-react'

interface CopyButtonProps {
  text: string
  label?: string
  className?: string
}

export const CopyButton: React.FC<CopyButtonProps> = ({ text, label, className = '' }) => {
  const [copied, setCopied] = useState(false)

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Fallback if clipboard API not available
      const textArea = document.createElement('textarea')
      textArea.value = text
      document.body.appendChild(textArea)
      textArea.select()
      document.execCommand('copy')
      document.body.removeChild(textArea)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  return (
    <button
      type="button"
      className={`btn-copy ${className}`}
      onClick={handleCopy}
      title={copied ? 'Copied to clipboard' : label ? `Copy ${label}` : 'Copy to clipboard'}
      aria-label={copied ? 'Copied' : label ? `Copy ${label}` : 'Copy'}
    >
      {copied ? (
        <Check size={13} style={{ color: 'var(--status-success)' }} />
      ) : (
        <Copy size={13} />
      )}
    </button>
  )
}
