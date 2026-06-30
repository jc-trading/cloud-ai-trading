# CloudAI Trading - Frontend Architecture

## Overview

The frontend has been redesigned to match a professional crypto trading platform (Binance-style) with a modern layout, sidebar navigation, and full dark/light mode support.

## Layout Structure

### AppLayout.vue
The main application layout with:
- **Sidebar Navigation** (left side)
  - Logo with app name
  - Primary navigation menu (System Monitoring, Market Data, AI & Trading, Account, Admin)
  - User profile card at the bottom
  - Responsive and collapsible on mobile

- **Top Header Bar** (sticky)
  - Current page title (dynamically mapped)
  - Last update timestamp
  - Theme toggle (dark/light mode)
  - User profile dropdown menu
  - Logout button in profile menu

- **Main Content Area**
  - Router outlet for page views
  - Responsive padding and margins
  - Theme-aware background colors

## Navigation Structure

```
/ (Dashboard) → "System Monitoring"
├── /market → "Market Overview"
├── /watchlist → "Watchlist Manager"
├── /analysis → "AI Analysis"
├── /strategies → "Trading Strategies"
├── /trading → "Live Trading"
├── /simulate → "Simulation"
├── /settings → "Settings & Profile"
├── /settings/exchange → "Exchange Connections"
└── /admin (if admin user)
    ├── /admin/users → "User Management"
    └── /admin/system → "System Administration"
```

## Theme System

### Dark Mode (Default)
- Background: `#0f172a` (gray-950)
- Sidebar: `#111827` (gray-900)
- Cards: `#111827` with `#1f2937` borders
- Text: White with gray accents
- Primary Color: Blue (`#3b82f6`)

### Light Mode
- Background: `#f9fafb` (gray-50)
- Sidebar: White with gray borders
- Cards: White with gray borders
- Text: Gray-900 with gray accents
- Primary Color: Blue (`#3b82f6`)

### Theme Persistence
- User's theme preference is saved to `localStorage` as `theme: 'dark' | 'light'`
- Default is dark mode if not set
- Theme is applied on app startup via `AppLayout.vue`

### Using Theme in Components
Components receive `isDarkMode` prop:
```vue
<div :class="isDarkMode ? 'bg-gray-900' : 'bg-white'">
  <!-- Content -->
</div>
```

## Key Components

### Pages (Views)
- **Dashboard.vue** → System Monitoring page
  - System health metrics
  - Task status panel
  - Real-time system logs
  - Auto-refresh every 5 seconds

- **Watchlist.vue** → Watchlist Manager page
  - Add/search symbols
  - Display watchlist items with prices
  - Remove symbols
  - Real-time price updates

- **Settings.vue** → Settings & Profile page
  - Profile settings (name, email, timezone, language, currency)
  - Preferences (theme, notifications, display options)
  - Security settings (2FA, API keys, password change)
  - Session management

- **Market.vue**, **Analysis.vue**, **Trading.vue**, etc.
  - All updated with English labels
  - Support for theme props

### Layout Components
- **SidebarItem.vue** - Navigation menu item with active state
  - Responds to theme changes
  - Shows active indicator

## Page Title Mapping

The AppLayout component maintains a mapping of route names to display titles:

```javascript
const pageTitles = {
  'Dashboard': 'System Monitoring',
  'Watchlist': 'Watchlist Manager',
  'Market': 'Market Overview',
  'Analysis': 'AI Analysis',
  'Strategies': 'Trading Strategies',
  'Trading': 'Live Trading',
  'Simulate': 'Simulation',
  'Settings': 'Settings & Profile',
  'ExchangeSettings': 'Exchange Connections',
  'AdminUsers': 'User Management',
  'AdminSystem': 'System Administration'
}
```

## English-First UI

All user-facing text is now in English by default:
- Menu labels: "System Monitoring", "Market Overview", "Watchlist Manager", etc.
- Button text: "Refresh All", "Logout", "Switch to Light Mode", etc.
- Page titles: Mapped from route names to readable English titles
- Messages and notifications: English-only currently

### Future: Language Switching
The Settings page includes a language selector for future implementation. The framework is ready to support:
- English (en) - default
- Spanish (es)
- French (fr)
- German (de)
- Chinese (zh)
- Japanese (ja)

To implement full i18n:
1. Install `vue-i18n`
2. Create translation files in `/locales/{lang}.json`
3. Update AppLayout to use i18n for all strings
4. Save user's language preference to both localStorage and database

## Component Props for Theme Support

All components that display UI should accept and use the `isDarkMode` prop:

```vue
<script setup>
defineProps({
  isDarkMode: {
    type: Boolean,
    default: true
  }
})
</script>
```

Then apply conditional classes:
```vue
<div :class="isDarkMode ? 'bg-gray-900 text-white' : 'bg-white text-gray-900'">
  <!-- Themed content -->
</div>
```

## Icons

The application uses PrimeIcons (`pi` class prefix). Common icons used:
- `pi-home` - Home/Dashboard
- `pi-chart-bar` - Market/Analytics
- `pi-star` - Watchlist
- `pi-cog` - Settings
- `pi-user` - User/Profile
- `pi-sign-out` - Logout
- `pi-sun` - Light mode toggle
- `pi-moon` - Dark mode toggle
- `pi-refresh` - Refresh
- `pi-clock` - Time
- `pi-globe` - Global/Market
- `pi-bolt` - Lightning/Trading

## Responsive Design

All pages and components use responsive grid layouts:
- Mobile-first approach
- Tailwind CSS responsive utilities (`md:`, `lg:`, etc.)
- Sidebar fixed on desktop, collapsible on mobile (future enhancement)
- Grid layouts adapt from 1 column (mobile) → 2-3 columns (tablet) → full layout (desktop)

## Color Palette

### Primary Colors
- Blue: `#3b82f6` - Buttons, links, active states
- Blue hover: `#2563eb` - Darker blue for interactions

### Neutral Colors (Dark Mode)
- Background: `#0f172a` (gray-950)
- Sidebar/Cards: `#111827` (gray-900)
- Borders: `#1f2937` (gray-800)
- Text primary: `#ffffff` (white)
- Text secondary: `#9ca3af` (gray-400)

### Neutral Colors (Light Mode)
- Background: `#f9fafb` (gray-50)
- Sidebar/Cards: `#ffffff` (white)
- Borders: `#e5e7eb` (gray-200)
- Text primary: `#111827` (gray-900)
- Text secondary: `#6b7280` (gray-600)

### Status Colors
- Success: `#10b981` (green-500)
- Warning: `#f59e0b` (amber-500)
- Danger: `#ef4444` (red-500)
- Info: `#06b6d4` (cyan-500)

## File Structure

```
frontend/src/
├── components/
│   ├── layout/
│   │   ├── AppLayout.vue          # Main layout wrapper
│   │   └── SidebarItem.vue        # Navigation item
│   ├── SystemMonitor.vue           # System health panel
│   ├── TaskStatusPanel.vue         # Task health status
│   ├── LogViewer.vue               # Log viewer component
│   └── WatchlistManager.vue        # Watchlist manager
├── views/
│   ├── Dashboard.vue               # System Monitoring page
│   ├── Watchlist.vue               # Watchlist Manager page
│   ├── Settings.vue                # Settings & Profile page
│   ├── Market.vue                  # Market Overview page
│   ├── Analysis.vue                # AI Analysis page
│   ├── Trading.vue                 # Live Trading page
│   ├── Simulate.vue                # Simulation page
│   ├── StrategyBuilder.vue         # Strategy Builder page
│   ├── SymbolDetail.vue            # Symbol Detail page
│   ├── ExchangeSettings.vue        # Exchange Settings page
│   ├── Login.vue                   # Login page (outside layout)
│   ├── Register.vue                # Register page (outside layout)
│   └── admin/                      # Admin pages
│       ├── Users.vue
│       └── System.vue
├── stores/
│   └── auth.ts                     # Authentication store
├── api.ts                          # API client
├── router/
│   └── index.js                    # Router configuration
└── main.js                         # App entry point
```

## Next Steps for Enhancement

1. **Mobile Optimization**
   - Make sidebar collapsible on mobile
   - Adjust header layout for small screens
   - Test touch interactions

2. **Dark Mode Improvements**
   - Ensure all components properly support both themes
   - Add theme transition animations
   - Test color contrast ratios for accessibility

3. **Internationalization (i18n)**
   - Implement `vue-i18n`
   - Create translation files for all languages
   - Add language selector in profile menu
   - Persist language preference

4. **Accessibility**
   - Add ARIA labels
   - Ensure keyboard navigation works
   - Test with screen readers
   - Ensure color contrast meets WCAG standards

5. **Performance**
   - Implement route-based code splitting
   - Add loading skeletons for data-heavy pages
   - Optimize images and assets

## Development Notes

- All new UI text should be in English
- Components should accept `isDarkMode` prop and use it for conditional styling
- Use Tailwind CSS for all styling (avoid inline styles)
- Theme preference is stored in `localStorage` under key `theme`
- The AppLayout component is the single source of truth for theme state

