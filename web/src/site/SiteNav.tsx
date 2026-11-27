import { useEffect, useId, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight, Menu, X } from 'lucide-react'
import { ButtonLink, Logo } from '../design'
import { CONSOLE_PATH, NAV_LINKS, REPOSITORY_URL } from './content'
import { GitHubMark } from './icons'
import styles from './SiteNav.module.css'

export function SiteNav() {
  const [open, setOpen] = useState(false)
  const menuId = useId()

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <header className={styles.nav}>
      <div className={`ak-container ${styles.bar}`}>
        <Link to="/" className={styles.brand} aria-label="Akesis home">
          <Logo />
        </Link>

        <div className={styles.pill}>
          <nav aria-label="Primary" className={styles.links}>
            {NAV_LINKS.map((link) => (
              <a key={link.href} href={link.href} className={styles.link}>{link.label}</a>
            ))}
          </nav>

          <div className={styles.actions}>
            <ButtonLink to={REPOSITORY_URL} variant="ghost" size="sm" leadingIcon={<GitHubMark />}>
              GitHub
            </ButtonLink>
            <ButtonLink to={CONSOLE_PATH} size="sm" trailingIcon={<ArrowUpRight size={14} />}>
              Open console
            </ButtonLink>
          </div>
        </div>

        <button
          type="button"
          className={styles.menuButton}
          aria-expanded={open}
          aria-controls={menuId}
          aria-label={open ? 'Close menu' : 'Open menu'}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? <X size={20} aria-hidden="true" /> : <Menu size={20} aria-hidden="true" />}
        </button>
      </div>

      <div id={menuId} className={styles.mobileMenu} hidden={!open}>
        <nav aria-label="Primary mobile" className="ak-container">
          {NAV_LINKS.map((link) => (
            <a key={link.href} href={link.href} className={styles.mobileLink} onClick={() => setOpen(false)}>
              {link.label}
            </a>
          ))}
          <div className={styles.mobileActions}>
            <ButtonLink to={CONSOLE_PATH} trailingIcon={<ArrowUpRight size={14} />}>Open console</ButtonLink>
            <ButtonLink to={REPOSITORY_URL} variant="secondary" leadingIcon={<GitHubMark />}>View on GitHub</ButtonLink>
          </div>
        </nav>
      </div>
    </header>
  )
}
