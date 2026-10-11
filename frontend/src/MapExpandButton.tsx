import React, { useEffect, useRef, useState } from 'react'
import { useMap } from 'react-leaflet'
import { Maximize2, Minimize2 } from 'lucide-react'
import L from 'leaflet'
import { t, type Lang } from './i18n'

/** Expand/collapse control for a map. Render it inside a MapContainer: it grows the
 * map's wrapper element (the container's parent) to fill the window, so overlays
 * and legends that live in that wrapper come along. */
export function MapExpandButton({ lang = 'en' }: { lang?: Lang }) {
  const i = t(lang)
  const map = useMap()
  const button = useRef<HTMLButtonElement>(null)
  const [expanded, setExpanded] = useState(false)

  // The button sits inside the map, so stop its clicks from reaching map click handlers.
  useEffect(() => {
    if (button.current) L.DomEvent.disableClickPropagation(button.current)
  }, [])

  useEffect(() => {
    const wrapper = map.getContainer().parentElement
    if (!wrapper) return
    const wheelZoom = map.scrollWheelZoom.enabled()
    wrapper.classList.toggle('map-expanded', expanded)
    map.invalidateSize()
    if (!expanded) return
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    map.scrollWheelZoom.enable()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setExpanded(false) }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = overflow
      if (!wheelZoom) map.scrollWheelZoom.disable()
      wrapper.classList.remove('map-expanded')
    }
  }, [expanded, map])

  const label = expanded ? i.mapCollapse : i.mapExpand
  return (
    <button ref={button} type="button" className="map-expand" onClick={() => setExpanded(v => !v)} title={label} aria-label={label} aria-pressed={expanded}>
      {expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
    </button>
  )
}
