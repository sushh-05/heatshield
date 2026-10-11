import React, { useCallback, useEffect, useRef, useState } from 'react'
import { CircleMarker, MapContainer, Rectangle, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import { AlertTriangle, CloudRain, Loader2, RefreshCw } from 'lucide-react'
import 'leaflet/dist/leaflet.css'
import { t, type Lang, type Translations } from './i18n'
import { MapExpandButton } from './MapExpandButton'
import { cellStyle, formatMm, lastHourReading, levelKey, spreadText, warningCount, type WaterloggingCell, type WaterloggingData } from './waterlogging'

type Props = {
  api: string
  lang: Lang
  latitude: number
  longitude: number
  canReport: boolean
  getToken: () => Promise<string | null>
}

type RadarFrame = { url: string; time: number }

// RainViewer's terms allow personal and educational use only, so the overlay stays off unless the operator opts in.
const RADAR_ENABLED = import.meta.env.VITE_ENABLE_RAINVIEWER === 'true'

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function levelLabel(i: Translations, risk: string): string {
  return i[levelKey(risk)]
}

function MapClicks({ onPick }: { onPick: (lat: number, lon: number) => void }) {
  useMapEvents({ click: e => onPick(e.latlng.lat, e.latlng.lng) })
  return null
}

function Recenter({ latitude, longitude }: { latitude: number; longitude: number }) {
  const map = useMap()
  useEffect(() => { map.setView([latitude, longitude], map.getZoom()) }, [latitude, longitude, map])
  return null
}

export function WaterloggingPanel({ api, lang, latitude, longitude, canReport, getToken }: Props) {
  const i = t(lang)
  const [data, setData] = useState<WaterloggingData | null>(null)
  const [inspected, setInspected] = useState<WaterloggingCell | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [feedback, setFeedback] = useState('')
  const [showRadar, setShowRadar] = useState(false)
  const [radar, setRadar] = useState<RadarFrame | null>(null)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = useCallback(async () => {
    try {
      const response = await fetch(`${api}/api/waterlogging?lat=${latitude}&lon=${longitude}`)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const payload: WaterloggingData = await response.json()
      setData(payload)
      setSelectedId(current => current ?? payload.focusCellId)
      setStatus('ready')
    } catch {
      // Keep showing the last good data, flagged by its own timestamp; only an empty panel becomes an error.
      setStatus(current => (current === 'ready' ? 'ready' : 'error'))
    }
  }, [api, latitude, longitude])

  useEffect(() => {
    setStatus('loading'); setData(null); setInspected(null); setSelectedId(null)
    load()
  }, [load])

  useEffect(() => {
    if (timer.current) clearInterval(timer.current)
    timer.current = setInterval(load, (data?.refreshSeconds || 600) * 1000)
    return () => { if (timer.current) clearInterval(timer.current) }
  }, [load, data?.refreshSeconds])

  useEffect(() => {
    if (!RADAR_ENABLED || !showRadar) return
    let cancelled = false
    fetch('https://api.rainviewer.com/public/weather-maps.json')
      .then(r => r.json())
      .then(maps => {
        const latest = maps?.radar?.past?.at(-1)
        if (!cancelled && latest?.path && typeof maps.host === 'string') {
          setRadar({ url: `${maps.host}${latest.path}/256/{z}/{x}/{y}/2/1_1.png`, time: latest.time })
        }
      })
      .catch(() => { if (!cancelled) setRadar(null) })
    return () => { cancelled = true }
  }, [showRadar, data?.updatedAt])

  const inspect = async (lat: number, lon: number) => {
    setBusy(true); setFeedback('')
    try {
      const response = await fetch(`${api}/api/waterlogging/point?lat=${lat.toFixed(4)}&lon=${lon.toFixed(4)}`)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const payload: WaterloggingData = await response.json()
      const cell = payload.cells[0]
      const known = data?.cells.some(c => c.id === cell.id)
      setInspected(known ? null : cell)
      setSelectedId(cell.id)
    } catch {
      setFeedback(i.wlUnavailable)
    } finally {
      setBusy(false)
    }
  }

  const cells = data ? [...data.cells, ...(inspected ? [inspected] : [])] : []
  const selected = cells.find(c => c.id === selectedId) || null

  const report = async () => {
    if (!selected) return
    setBusy(true); setFeedback('')
    try {
      const token = await getToken()
      const response = await fetch(`${api}/api/waterlogging/reports`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ lat: selected.lat, lon: selected.lon, note }),
      })
      if (!response.ok) { setFeedback(response.status === 401 ? i.signInRequired : i.actionFailed); return }
      setNote('')
      await load()
      if (inspected) await inspect(selected.lat, selected.lon)
      setFeedback(i.wlReportSaved)
    } catch {
      setFeedback(i.serverUnreachable)
    } finally {
      setBusy(false)
    }
  }

  const alerts = new Map<string, WaterloggingCell['nearbyAlerts'][number]>()
  cells.forEach(c => c.nearbyAlerts.forEach(a => alerts.set(a.id, a)))
  const warnings = data ? warningCount(data.cells) : 0

  return (
    <section className="panel wl-panel">
      <div className="panel-head">
        <div>
          <h2><CloudRain size={17} /> {i.wlTitle}</h2>
          <p>{i.wlSubtitle}</p>
        </div>
        <div className="wl-freshness">
          {data && <span>{i.wlUpdated(clock(data.updatedAt))} · {i.wlRefreshes(Math.round(data.refreshSeconds / 60))}</span>}
          <button type="button" className="resolve-btn" onClick={load} disabled={busy} aria-label={i.wlRetry}><RefreshCw size={12} /></button>
        </div>
      </div>

      {status === 'loading' && <div className="wl-state"><Loader2 size={18} className="spin" /> {i.wlLoading}</div>}
      {status === 'error' && (
        <div className="wl-state" role="alert">
          {i.wlUnavailable} <button type="button" className="resolve-btn" onClick={() => { setStatus('loading'); load() }}>{i.wlRetry}</button>
        </div>
      )}

      {data && (
        <>
          {warnings > 0 && <div className="wl-warning" role="alert"><AlertTriangle size={15} /> {i.wlWarning(warnings)}</div>}
          <div className="wl-body">
            <div className="wl-map">
              <MapContainer center={[latitude, longitude]} zoom={10} minZoom={2} scrollWheelZoom={false} style={{ height: 420, width: '100%' }}>
                <TileLayer
                  attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors | Weather data by <a href="https://open-meteo.com/">Open-Meteo.com</a> | Station reports: NOAA AWC | Flood alerts: <a href="https://www.gdacs.org/">GDACS</a>'
                  url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                />
                {RADAR_ENABLED && showRadar && radar && (
                  <TileLayer key={radar.url} url={radar.url} opacity={0.6} maxNativeZoom={7} attribution='Radar: <a href="https://www.rainviewer.com/">RainViewer</a>' />
                )}
                <Recenter latitude={latitude} longitude={longitude} />
                <MapClicks onPick={inspect} />
                <MapExpandButton lang={lang} />
                {cells.map(cell => (
                  <Rectangle
                    key={`${cell.id}-${cell.risk}-${cell.id === selectedId}`}
                    bounds={cell.bounds}
                    pathOptions={{ ...cellStyle(cell.risk, cell.id === selectedId), bubblingMouseEvents: false }}
                    eventHandlers={{ click: () => { setSelectedId(cell.id); setFeedback('') } }}
                  >
                    <Tooltip sticky>
                      {levelLabel(i, cell.risk)} · {cell.rainfall ? `${formatMm(cell.rainfall.last24hMm)} / 24 h` : i.wlNoRainfall}
                    </Tooltip>
                  </Rectangle>
                ))}
                {[...alerts.values()].map(alert => (
                  <CircleMarker key={alert.id} center={[alert.lat, alert.lon]} radius={9} pathOptions={{ color: '#1d4ed8', weight: 2, fillColor: '#fff', fillOpacity: 0.9, dashArray: '3 3' }}>
                    <Tooltip>{alert.name} · {alert.alertLevel}</Tooltip>
                  </CircleMarker>
                ))}
                {data.reports.map(r => (
                  <CircleMarker key={r.id} center={[r.lat, r.lon]} radius={6} pathOptions={{ color: '#fff', weight: 2, fillColor: r.verified ? '#6d3fd1' : '#8c8576', fillOpacity: 1 }}>
                    <Tooltip>{i.wlLegendReport} · {r.verified ? i.wlReportVerified : i.wlReportUnverified} · {clock(r.at)}</Tooltip>
                  </CircleMarker>
                ))}
              </MapContainer>
              <div className="wl-legend">
                {(['LOW', 'MODERATE', 'HIGH', 'VERY_HIGH', 'CONFIRMED', 'INSUFFICIENT_DATA'] as const).map(level => (
                  <span key={level}><i style={{ background: cellStyle(level, false).fillColor, opacity: level === 'INSUFFICIENT_DATA' ? 0.5 : 1 }} />{levelLabel(i, level)}</span>
                ))}
                <span><i className="wl-dot alert" />{i.wlLegendAlert}</span>
                <span><i className="wl-dot report" />{i.wlLegendReport}</span>
                {RADAR_ENABLED && (
                  <label><input type="checkbox" checked={showRadar} onChange={e => setShowRadar(e.target.checked)} /> {i.wlRadar}{showRadar && radar ? ` · ${i.wlRadarTime(clock(new Date(radar.time * 1000).toISOString()))}` : ''}</label>
                )}
              </div>
              <p className="wl-note">{i.wlGridNote(data.gridKm)} {i.wlSelectHint}</p>
            </div>

            <div className="wl-details" aria-live="polite">
              {selected ? (
                <>
                  <h3>{i.wlAreaTitle(`${selected.lat.toFixed(1)}°`, `${selected.lon.toFixed(1)}°`)}</h3>
                  <div className="wl-levels">
                    <div>
                      <span>{selected.kind === 'CONFIRMED' ? i.wlConfirmed : `${i.wlEstimated} · ${i.wlNow}`}</span>
                      <b className="wl-badge" style={{ background: cellStyle(selected.risk, false).fillColor }}>{levelLabel(i, selected.risk)}</b>
                    </div>
                    <div>
                      <span>{i.wlEstimated} · {i.wlNext24h}</span>
                      <b className="wl-badge" style={{ background: cellStyle(selected.forecastRisk, false).fillColor }}>{levelLabel(i, selected.forecastRisk)}</b>
                    </div>
                    <div>
                      <span>{i.wlConfidence}</span>
                      <b>{{ none: i.wlConfNone, low: i.wlConfLow, medium: i.wlConfMedium, high: i.wlConfHigh }[selected.confidence.label]}</b>
                    </div>
                  </div>

                  {selected.rainfall ? (
                    <>
                    <h4 className="wl-rain-head">{i.wlModelled}</h4>
                    <p className={`wl-station ${selected.rainfall.lastHourCheck === 'CONTRADICTED' ? 'contradicted' : ''}`}>
                      {selected.observation
                        ? (selected.observation.rainReported ? i.wlStationWet : i.wlStationDry)(selected.observation.stationName, selected.observation.distanceKm, clock(selected.observation.observedAt))
                        : i.wlNoStation}
                    </p>
                    <dl className="wl-rain">
                      <div>
                        <dt>{i.wlRainLastHour}</dt>
                        {lastHourReading(selected) === 'MODEL' ? (
                          <dd>{formatMm(selected.rainfall.lastHourMm)}{spreadText(selected.rainfall.modelSpread?.lastHourMm) && <small>{i.wlModelsRange(spreadText(selected.rainfall.modelSpread?.lastHourMm)!)}</small>}</dd>
                        ) : (
                          <dd>{lastHourReading(selected) === 'RAIN_OBSERVED' ? i.wlRainObserved : i.wlNoRainObserved}<small>{i.wlModelSays(formatMm(selected.rainfall.lastHourMm))}</small></dd>
                        )}
                      </div>
                      <div><dt>{i.wlRainPeak}</dt><dd>{formatMm(selected.rainfall.peakLast3hMmPerHour)}/h</dd></div>
                      <div><dt>{i.wlRain6h}</dt><dd>{formatMm(selected.rainfall.last6hMm)}</dd></div>
                      <div><dt>{i.wlRain24h}</dt><dd>{formatMm(selected.rainfall.last24hMm)}{spreadText(selected.rainfall.modelSpread?.last24hMm) && <small>{i.wlModelsRange(spreadText(selected.rainfall.modelSpread?.last24hMm)!)}</small>}</dd></div>
                      <div><dt>{i.wlWetHours}</dt><dd>{selected.rainfall.wetHoursLast24h}</dd></div>
                      <div><dt>{i.wlForecast6h}</dt><dd>{formatMm(selected.rainfall.forecastNext6hMm)}</dd></div>
                      <div><dt>{i.wlForecast24h}</dt><dd>{formatMm(selected.rainfall.forecastNext24hMm)}</dd></div>
                    </dl>
                    </>
                  ) : <p className="wl-empty">{i.wlNoRainfall}</p>}

                  {selected.reference && (
                    <div className="wl-block">
                      <h4>{i.wlReference(selected.reference.days)}</h4>
                      <ul>
                        {(['MODERATE', 'HIGH', 'VERY_HIGH'] as const).map(level => (
                          <li key={level}>{i.wlReferenceRow(levelLabel(i, level), selected.reference!.accumulation24hMm[level], selected.reference!.intensityMmPerHour[level])}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {selected.evidence.length > 0 && (
                    <div className="wl-block"><h4>{i.wlEvidence}</h4><ul>{selected.evidence.map(text => <li key={text}>{text}</li>)}</ul></div>
                  )}
                  {selected.nearbyAlerts.length > 0 && (
                    <div className="wl-block">
                      <h4>{i.wlAlerts}</h4>
                      <ul>{selected.nearbyAlerts.map(a => (
                        <li key={a.id}>{a.url ? <a href={a.url} target="_blank" rel="noopener noreferrer">{i.wlAlertRow(a.name, a.alertLevel, a.distanceKm)}</a> : i.wlAlertRow(a.name, a.alertLevel, a.distanceKm)}</li>
                      ))}</ul>
                    </div>
                  )}
                  {(selected.confirmedReports.length > 0 || selected.unverifiedReports.length > 0) && (
                    <div className="wl-block">
                      <h4>{i.wlReports}</h4>
                      <ul>{[...selected.confirmedReports, ...selected.unverifiedReports].map(r => (
                        <li key={r.id}>{clock(r.at)} · {r.reportedBy} · {r.verified ? i.wlReportVerified : i.wlReportUnverified}{r.note ? ` · ${r.note}` : ''}</li>
                      ))}</ul>
                    </div>
                  )}
                  {selected.limitations.length > 0 && (
                    <div className="wl-block muted"><h4>{i.wlLimitations}</h4><ul>{selected.limitations.map(text => <li key={text}>{text}</li>)}</ul></div>
                  )}

                  {canReport && (
                    <div className="wl-report">
                      <input value={note} onChange={e => setNote(e.target.value)} placeholder={i.wlReportNote} maxLength={280} aria-label={i.wlReportNote} />
                      <button type="button" className="resolve-btn" onClick={report} disabled={busy}>{i.wlReportButton}</button>
                    </div>
                  )}
                  {feedback && <small className="feedback">{feedback}</small>}
                </>
              ) : <p className="wl-empty">{i.wlSelectHint}</p>}
            </div>
          </div>

          <div className="wl-sources">
            <strong>{i.wlSources}:</strong>
            {data.sources.map(source => (
              <span key={source.id} title={`${source.observes} ${source.licence}`}>
                {source.name}
              </span>
            ))}
          </div>
        </>
      )}
    </section>
  )
}
