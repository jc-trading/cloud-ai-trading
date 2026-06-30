# Frontend UI Rules

This project may reference the local Judia Vue template for layout and visual direction, but it must not copy the whole template into the app.

## Approved Judia Preset

- Select Layouts: Vertical
- Color Scheme: Dark
- Card Layout: Borderless
- Layout Width: Boxed
- Layout Position: Scrollable
- Sidebar Size: Default
- Sidebar Color: Dark
- Topbar Color: Dark
- Topbar Images: One
- Preloader: Enable

## What To Reuse

- Layout semantics from Judia data attributes: `data-layout`, `data-bs-theme`, `data-card-layout`, `data-layout-width`, `data-layout-position`, `data-sidebar-size`, `data-sidebar`, `data-topbar`, `data-topbar-image`, `data-preloader`.
- High-level layout behavior: vertical sidebar, dark sidebar, dark topbar, boxed app shell, page-level scrolling, borderless cards, short preloader.
- Visual direction: dark admin dashboard surfaces, subdued separators, compact navigation, one restrained topbar pattern.
- Spacing and sizing ideas when they fit our existing Vue + Tailwind structure.

## What To Skip

- Do not copy Judia example pages, demo dashboards, auth pages, invoices, calendars, maps, tables, charts, widgets, ecommerce, CRM, or file-manager screens.
- Do not import Judia's Bootstrap, BootstrapVue, Vuex store, i18n system, right-side customizer, generated dist assets, or large icon/font bundles.
- Do not copy large SVG pattern blocks or image packs unless a specific asset is needed for a product feature.
- Do not add template-only dependencies just because Judia uses them.
- Do not replace existing product routes or business components with Judia demos.

## Project Implementation Rules

- Keep the app on Vue 3, Vite, Pinia, Tailwind, Ant Design Vue, and PrimeVue unless a feature requires otherwise.
- New UI should use the existing `AppLayout` shell and the approved preset above.
- Prefer Tailwind utility classes and local CSS variables in `src/assets/main.css` over importing Judia SCSS wholesale.
- Cards should be visually borderless by default in the approved preset; use borders only when the boundary is functionally useful.
- Topbar and sidebar stay dark. Do not introduce light, brand, horizontal, detached, two-column, or compact-sidebar variants unless explicitly requested.
- Layout remains boxed on desktop and scrollable, with responsive behavior on smaller screens.
- Preloader is allowed as a short app-start overlay only. Do not block route changes with long loaders.

## Template Reference Location

Local reference folder:

`/Users/jiacong/JiaCong/Developer notes/ProjectTemplates/themeforest-B2ZADQQa-judia-admin-dashboard-template/Admin/Vue/`
