import React, { useState } from 'react'
import type { IncidentDetail } from '../../types/api'
import { CopyButton } from '../Common/CopyButton'
import {
  AlertOctagon,
  ChevronDown,
  ChevronUp,
  Clock,
  Terminal,
} from 'lucide-react'

interface DockedTerminalProps {
  incident: IncidentDetail
}

export const DockedTerminal: React.FC<DockedTerminalProps> = ({ incident }) => {
  const [activeTab, setActiveTab] = useState<'validation' | 'stdout' | 'stderr' | 'evidence'>('validation')
  const [isExpanded, setIsExpanded] = useState<boolean>(true)

  const validation = incident.validation_result
  const stdout = incident.sandbox_stdout || validation?.stdout || ''
  const stderr = incident.sandbox_stderr || validation?.stderr || ''
  const traceback = incident.traceback_excerpt || incident.failure_context?.raw_log_excerpt || ''
  const command = incident.sandbox_command || validation?.command_executed || ''
  const exitCode = incident.sandbox_exit_code ?? validation?.exit_code ?? null

  const getCurrentContentToCopy = (): string => {
    switch (activeTab) {
      case 'validation':
        return `Command: ${command}\nExit Code: ${exitCode}\nStatus: ${validation?.status || 'N/A'}`
      case 'stdout':
        return stdout
      case 'stderr':
        return stderr
      case 'evidence':
        return traceback
      default:
        return ''
    }
  }

  return (
    <div className="docked-terminal-panel" aria-label="Docked Terminal Console">
      {/* Terminal Toolbar Strip */}
      <div className="docked-terminal-header">
        <div className="docked-terminal-tabs">
          <button
            type="button"
            className={`docked-terminal-tab ${activeTab === 'validation' ? 'active' : ''}`}
            onClick={() => {
              setActiveTab('validation')
              if (!isExpanded) setIsExpanded(true)
            }}
          >
            <Terminal size={12} />
            <span>Validation</span>
            {exitCode !== null && (
              <span className={`terminal-tab-dot ${exitCode === 0 ? 'is-pass' : 'is-fail'}`} />
            )}
          </button>

          <button
            type="button"
            className={`docked-terminal-tab ${activeTab === 'stdout' ? 'active' : ''}`}
            onClick={() => {
              setActiveTab('stdout')
              if (!isExpanded) setIsExpanded(true)
            }}
          >
            <span>Stdout</span>
            {stdout && (
              <span className="mono terminal-count">({stdout.split('\n').length}L)</span>
            )}
          </button>

          <button
            type="button"
            className={`docked-terminal-tab ${activeTab === 'stderr' ? 'active' : ''}`}
            onClick={() => {
              setActiveTab('stderr')
              if (!isExpanded) setIsExpanded(true)
            }}
          >
            <span>Stderr</span>
            {stderr && (
              <span className="mono terminal-count is-fail">({stderr.split('\n').length}L)</span>
            )}
          </button>

          <button
            type="button"
            className={`docked-terminal-tab ${activeTab === 'evidence' ? 'active' : ''}`}
            onClick={() => {
              setActiveTab('evidence')
              if (!isExpanded) setIsExpanded(true)
            }}
          >
            <AlertOctagon size={12} />
            <span>Failure Evidence</span>
          </button>
        </div>

        <div className="terminal-toolbar-end">
          {isExpanded && getCurrentContentToCopy() && (
            <CopyButton text={getCurrentContentToCopy()} label="Console Output" />
          )}

          <button
            type="button"
            className="btn btn-sm btn-icon"
            onClick={() => setIsExpanded((prev) => !prev)}
            title={isExpanded ? 'Collapse console' : 'Expand console'}
            aria-label={isExpanded ? 'Collapse console' : 'Expand console'}
          >
            {isExpanded ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
          </button>
        </div>
      </div>

      {/* Terminal Body Content */}
      {isExpanded && (
        <div className="docked-terminal-body">
          {activeTab === 'validation' && (
            <div className="terminal-tab-panel">
              <div className="terminal-meta">
                <span className="terminal-meta-item">
                  <span className="terminal-meta-label">$</span>
                  <span className="terminal-command">{command || 'not recorded'}</span>
                </span>

                <span className="terminal-meta-item">
                  <span className="terminal-meta-label">exit</span>
                  <span
                    className={`mono terminal-exit ${
                      exitCode === 0 ? 'is-pass' : exitCode !== null ? 'is-fail' : ''
                    }`}
                  >
                    {exitCode !== null ? exitCode : '—'}
                  </span>
                </span>

                {validation?.duration_ms !== undefined && (
                  <span className="terminal-meta-item">
                    <Clock size={11} />
                    <span className="mono">{validation.duration_ms.toFixed(1)}ms</span>
                  </span>
                )}

                {validation?.status && (
                  <span className="terminal-meta-item">
                    <span className="terminal-meta-label">outcome</span>
                    <span className={`mono terminal-exit ${validation.status === 'passed' ? 'is-pass' : 'is-fail'}`}>
                      {validation.status.toUpperCase()}
                    </span>
                  </span>
                )}
              </div>

              {validation?.failure_reason && (
                <div className="terminal-failure">
                  <strong>Failure:</strong> {validation.failure_reason}
                </div>
              )}

              {stdout ? (
                <pre className="terminal-view terminal-output">{stdout}</pre>
              ) : (
                <p className="terminal-empty">No stdout logs captured during validation.</p>
              )}
            </div>
          )}

          {activeTab === 'stdout' && (
            <div className="terminal-tab-panel">
              {stdout ? (
                <pre className="terminal-view terminal-output">{stdout}</pre>
              ) : (
                <p className="terminal-empty">No stdout log stream captured.</p>
              )}
            </div>
          )}

          {activeTab === 'stderr' && (
            <div className="terminal-tab-panel">
              {stderr ? (
                <pre className="terminal-view terminal-view-stderr terminal-output">{stderr}</pre>
              ) : (
                <p className="terminal-empty">No stderr errors captured (clean process output).</p>
              )}
            </div>
          )}

          {activeTab === 'evidence' && (
            <div className="terminal-tab-panel">
              {traceback ? (
                <pre className="terminal-view terminal-output">{traceback}</pre>
              ) : (
                <p className="terminal-empty">No raw log excerpt or stack trace recorded for this incident.</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
