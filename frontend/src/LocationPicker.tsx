import React, { useEffect, useRef, useState } from 'react'
import { MapContainer, Marker, TileLayer, ZoomControl, useMap, useMapEvents } from 'react-leaflet'
import { Crosshair, Loader2, MapPin, Search } from 'lucide-react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { t, type Lang } from './i18n'
import { MapExpandButton } from './MapExpandButton'

/* Leaflet's default marker icon references image files the bundler won't
 * resolve automatically; build a small inline SVG pin instead. */
const pinIcon = L.divIcon({
  className: 'map-pin-icon',
  html: '<svg width="30" height="38" viewBox="0 0 30 38" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M15 0C6.7 0 0 6.6 0 14.8 0 25.5 15 38 15 38s15-12.5 15-23.2C30 6.6 23.3 0 15 0z" fill="#e2a53a"/><circle cx="15" cy="14.5" r="5.5" fill="#12151b"/></svg>',
  iconSize: [30, 38],
  iconAnchor: [15, 38],
})

type GeocodeResult = { display_name: string; lat: string; lon: string }

type Props = {
  latitude: number
  longitude: number
  onChange: (lat: number, lon: number, label?: string) => void
  lang?: Lang
}

/** Captures map clicks and drops the marker at the clicked coordinate. */
function ClickToPlace({ onPick }: { onPick: (lat: number, lon: number) => void }) {
  useMapEvents({
    click(e) {
      onPick(e.latlng.lat, e.latlng.lng)
    },
  })
  return null
}

/** MapContainer only reads center/zoom on first mount, so a search or drag
 * that updates lat/lng afterwards needs to pan the map imperatively. */
function RecenterMap({ latitude, longitude }: { latitude: number; longitude: number }) {
  const map = useMap()
  useEffect(() => {
    map.setView([latitude, longitude], map.getZoom())
  }, [latitude, longitude, map])
  return null
}

export function LocationPicker({ latitude, longitude, onChange, lang = 'en' }: Props) {
  const i = t(lang)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<GeocodeResult[]>([])
  const [searching, setSearching] = useState(false)
  const [showResults, setShowResults] = useState(false)
  const [locating, setLocating] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const latestQuery = useRef('')

  const search = (value: string) => {
    latestQuery.current = value
    setQuery(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    if (value.trim().length < 3) {
      setResults([])
      return
    }
    debounceRef.current = setTimeout(async () => {
      setSearching(true)
      try {
        const r = await fetch(
          `https://nominatim.openstreetmap.org/search?format=json&limit=5&q=${encodeURIComponent(value)}`,
          { headers: { Accept: 'application/json' } },
        )
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const payload: GeocodeResult[] = await r.json()
        // A slower answer to an earlier query must not replace the results of what is typed now.
        if (latestQuery.current !== value) return
        setResults(Array.isArray(payload) ? payload : [])
        setShowResults(true)
      } catch {
        setResults([])
      } finally {
        setSearching(false)
      }
    }, 450)
  }

  const pickResult = (r: GeocodeResult) => {
    const lat = parseFloat(r.lat)
    const lon = parseFloat(r.lon)
    onChange(lat, lon, r.display_name)
    setQuery(r.display_name)
    setShowResults(false)
  }

  const locateMe = () => {
    if (!navigator.geolocation) return
    setLocating(true)
    navigator.geolocation.getCurrentPosition(
      pos => {
        onChange(pos.coords.latitude, pos.coords.longitude)
        setQuery('')
        setLocating(false)
      },
      () => setLocating(false),
      { timeout: 8000 },
    )
  }

  useEffect(() => () => { if (debounceRef.current) clearTimeout(debounceRef.current) }, [])

  return (
    <div className="location-picker">
      <div className="location-map">
        <MapContainer
          center={[latitude, longitude]}
          zoom={13}
          scrollWheelZoom={false}
          zoomControl={false}
          style={{ height: '380px', width: '100%' }}
        >
          <ZoomControl position="bottomright" />
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <Marker
            position={[latitude, longitude]}
            icon={pinIcon}
            draggable
            eventHandlers={{
              dragend: e => {
                const pos = (e.target as L.Marker).getLatLng()
                onChange(pos.lat, pos.lng)
              },
            }}
          />
          <ClickToPlace onPick={(lat, lon) => onChange(lat, lon)} />
          <RecenterMap latitude={latitude} longitude={longitude} />
          <MapExpandButton lang={lang} />
        </MapContainer>

        {/* Search + locate float above the map, like Uber/DoorDash/Airbnb address pickers */}
        <div className="location-search">
          <Search size={15} className="location-search-icon" />
          <input
            value={query}
            onChange={e => search(e.target.value)}
            onFocus={() => results.length > 0 && setShowResults(true)}
            // Enter here would submit the whole site form with the old coordinates; pick the first match instead.
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); if (results[0]) pickResult(results[0]) } }}
            placeholder={i.locationSearchPlaceholder}
            aria-label={i.locationSearchPlaceholder}
          />
          {searching && <Loader2 size={15} className="location-search-spinner spin" />}
          {showResults && results.length > 0 && (
            <ul className="location-results">
              {results.map(r => (
                <li key={`${r.lat}-${r.lon}-${r.display_name}`}>
                  <button type="button" onClick={() => pickResult(r)}>
                    <MapPin size={13} /> <span>{r.display_name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <button type="button" className="location-locate" onClick={locateMe} title={i.locationUseMine} aria-label={i.locationUseMine}>
          <Crosshair size={16} className={locating ? 'spin' : ''} />
        </button>

        <div className="location-coords">
          <MapPin size={13} /> {latitude.toFixed(4)}, {longitude.toFixed(4)}
        </div>
      </div>
      <p className="location-hint">{i.locationHint}</p>
    </div>
  )
}
