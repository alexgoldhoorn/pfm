# Portfolio Management Web Application - Wireframe Specifications

## 📋 Wireframe Overview

This document outlines the wireframe specifications for each page of the Portfolio Management web application. While actual wireframes should be created in Figma or similar tools, this serves as a comprehensive guide for UI/UX designers and developers.

## 🎯 Design Principles

### User Experience Goals
- **Clarity**: Information hierarchy should be immediately clear
- **Efficiency**: Common tasks should require minimal clicks
- **Accessibility**: WCAG 2.1 AA compliance throughout
- **Responsiveness**: Mobile-first design approach
- **Performance**: Fast loading and smooth interactions

### Visual Design System
- **Color Palette**: Neutral base with accent colors for states
- **Typography**: Clear hierarchy with system fonts
- **Spacing**: 8px grid system for consistent layouts
- **Components**: Reusable design components
- **Icons**: Consistent icon library (e.g., Heroicons, Lucide)

---

## 📱 Responsive Breakpoints

```css
/* Mobile First Approach */
@media (min-width: 640px)  { /* sm */ }
@media (min-width: 768px)  { /* md */ }
@media (min-width: 1024px) { /* lg */ }
@media (min-width: 1280px) { /* xl */ }
@media (min-width: 1536px) { /* 2xl */ }
```

---

## 1. 🔐 Authentication Pages

### Login Page
```
┌─────────────────────────────────────────┐
│ [LOGO] Portfolio Manager                │
├─────────────────────────────────────────┤
│                                         │
│     ┌─────────────────────────────┐     │
│     │        Sign In              │     │
│     ├─────────────────────────────┤     │
│     │ Email: [________________]   │     │
│     │ Password: [_____________]   │     │
│     │ [ ] Remember me             │     │
│     │                             │     │
│     │ [    Sign In Button    ]    │     │
│     │                             │     │
│     │ Forgot password?            │     │
│     │ New user? Sign up           │     │
│     └─────────────────────────────┘     │
│                                         │
└─────────────────────────────────────────┘
```

**Key Components:**
- Logo/branding area
- Email input field with validation
- Password input field with show/hide toggle
- "Remember me" checkbox
- Primary sign-in button
- Links to password reset and registration
- Error message area (hidden by default)

**States:**
- Default state
- Loading state (disabled inputs, spinner on button)
- Error state (red border, error message)
- Success state (redirect to dashboard)

### Registration Page
```
┌─────────────────────────────────────────┐
│ [LOGO] Portfolio Manager                │
├─────────────────────────────────────────┤
│                                         │
│     ┌─────────────────────────────┐     │
│     │       Create Account        │     │
│     ├─────────────────────────────┤     │
│     │ Full Name: [____________]   │     │
│     │ Email: [________________]   │     │
│     │ Username: [_____________]   │     │
│     │ Password: [_____________]   │     │
│     │ Confirm: [______________]   │     │
│     │ [ ] Accept Terms of Service │     │
│     │                             │     │
│     │ [   Create Account    ]     │     │
│     │                             │     │
│     │ Already have account?       │     │
│     └─────────────────────────────┘     │
│                                         │
└─────────────────────────────────────────┘
```

---

## 2. 📊 Dashboard Page

### Desktop Layout
```
┌─────────────────────────────────────────────────────────────────┐
│ [LOGO] Portfolio  [Search] [Notifications] [User Menu]         │
├─────────────────────────────────────────────────────────────────┤
│ ◄ Sidebar ►│ Main Content Area                                 │
│            │ ┌─────────────────────────────────────────────┐   │
│ • Dashboard│ │ Portfolio Summary                           │   │
│ • Portfolio│ │ Total Value: $125,430.50 (+2.3%)          │   │
│ • Trans.   │ │ Today's Change: +$2,845.20                 │   │
│ • Assets   │ │ [Chart: Portfolio Performance]             │   │
│ • Reports  │ └─────────────────────────────────────────────┘   │
│ • Settings │                                                   │
│            │ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ │
│            │ │ Top Holdings│ │ Recent      │ │ Market      │ │
│            │ │ AAPL $15.2K │ │ Transactions│ │ Overview    │ │
│            │ │ MSFT $12.8K │ │ Buy TSLA... │ │ S&P +0.5%   │ │
│            │ │ GOOGL $10.1K│ │ Sell AMZN.. │ │ NASDAQ +0.8%│ │
│            │ └─────────────┘ └─────────────┘ └─────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### Mobile Layout
```
┌───────────────────────────┐
│ ☰ [LOGO] Portfolio    🔔 │
├───────────────────────────┤
│ Portfolio Summary         │
│ Total Value: $125,430.50  │
│ Today: +$2,845.20 (+2.3%) │
│ [Mini Chart]              │
├───────────────────────────┤
│ Quick Actions             │
│ [Add Transaction] [View]  │
├───────────────────────────┤
│ Top Holdings              │
│ • AAPL    $15,200 (+1.2%) │
│ • MSFT    $12,800 (+0.8%) │
│ • GOOGL   $10,100 (-0.3%) │
├───────────────────────────┤
│ Recent Activity           │
│ • Buy TSLA - $2,500       │
│ • Dividend MSFT - $25     │
└───────────────────────────┘
```

**Key Components:**
- Portfolio value summary with percentage change
- Performance chart (time period selector)
- Top holdings list with current values
- Recent transactions preview
- Market overview widget
- Quick action buttons
- Navigation sidebar (desktop) or hamburger menu (mobile)

---

## 3. 📂 Portfolio Management Page

### Holdings View
```
┌─────────────────────────────────────────────────────────────────┐
│ Portfolio Details                                    [Edit] [⚙] │
├─────────────────────────────────────────────────────────────────┤
│ My Portfolio                          Value: $125,430.50        │
│ Cash: $5,230.00                      Change: +$2,845.20 (+2.3%) │
├─────────────────────────────────────────────────────────────────┤
│ Holdings                             [Search: ________] [Filter]│
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │Symbol│Name           │Shares │Avg Cost│Current│Value   │P/L  │ │
│ ├─────────────────────────────────────────────────────────────┤ │
│ │AAPL  │Apple Inc.     │100    │$150.00 │$152.00│$15,200 │+2.0%│ │
│ │MSFT  │Microsoft Corp │80     │$160.00 │$160.80│$12,864 │+0.5%│ │
│ │GOOGL │Alphabet Inc.  │35     │$140.00 │$142.50│$4,987  │+1.8%│ │
│ └─────────────────────────────────────────────────────────────┘ │
│ [Previous] [1] [2] [3] [Next]                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Key Components:**
- Portfolio header with total value and P&L
- Cash balance display
- Holdings table with sortable columns
- Search and filter functionality
- Pagination controls
- Actions menu for each holding

---

## 4. 💰 Transaction Management Page

### Transaction History
```
┌─────────────────────────────────────────────────────────────────┐
│ Transactions                                         [+ Add New] │
├─────────────────────────────────────────────────────────────────┤
│ Filters: [All Types ▼] [All Assets ▼] [Date Range]   [Export]   │
├─────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │Date     │Type│Symbol│Quantity│Price  │Total    │Fees      │⚙│ │
│ ├─────────────────────────────────────────────────────────────┤ │
│ │09/15/25 │Buy │AAPL  │50      │$152.00│$7,600.00│$9.99     │⚙│ │
│ │09/14/25 │Sell│TSLA  │25      │$240.00│$6,000.00│$12.50    │⚙│ │
│ │09/10/25 │Div │MSFT  │-       │-      │$25.00   │$0.00     │⚙│ │
│ └─────────────────────────────────────────────────────────────┘ │
│ [Previous] [1] [2] [3] [Next]                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Add Transaction Form
```
┌─────────────────────────────────────────┐
│ Add New Transaction                  [×]│
├─────────────────────────────────────────┤
│ Type: [Buy        ▼]                   │
│ Asset: [Search assets...________]      │
│ Quantity: [________] shares            │
│ Price: $[_______] per share            │
│ Date: [09/16/2025] [📅]                │
│ Fees: $[_______] (optional)            │
│ Notes: [________________]              │
│ ────────────────────────              │
│ Total: $7,609.99                       │
│ ────────────────────────              │
│ [Cancel]              [Save Transaction]│
└─────────────────────────────────────────┘
```

---

## 5. 🔍 Asset Explorer Page

### Asset Search & Details
```
┌─────────────────────────────────────────────────────────────────┐
│ Asset Explorer                                        [+ Watchlist]│
├─────────────────────────────────────────────────────────────────┤
│ Search: [AAPL___________________________] [🔍]                  │
├─────────────────────────────────────────────────────────────────┤
│ Apple Inc. (AAPL)                                    NASDAQ      │
│ Current Price: $152.00 (+$2.00, +1.33%)                        │
│ ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐    │
│ │ Price Chart     │ │ Key Metrics     │ │ Recent News     │    │
│ │ [Chart Display] │ │ P/E: 28.5      │ │ • Apple reports │    │
│ │ [1D][1W][1M][1Y]│ │ Market Cap:     │ │   strong Q3...  │    │
│ │                 │ │ $2.85T          │ │ • New iPhone    │    │
│ │                 │ │ Dividend: 0.96% │ │   launch...     │    │
│ └─────────────────┘ └─────────────────┘ └─────────────────┘    │
│ [Add to Watchlist] [Buy] [View Full Analysis]                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. 🤖 AI Assistant Page

### Chat Interface
```
┌─────────────────────────────────────────────────────────────────┐
│ AI Portfolio Assistant                               [Clear Chat]│
├─────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ 🤖 Hi! I can help analyze your portfolio, suggest          │ │
│ │    investments, and answer questions about your holdings.  │ │
│ │                                                             │ │
│ │ 👤 How is my portfolio performing this month?              │ │
│ │                                                             │ │
│ │ 🤖 Your portfolio is up 3.2% this month, outperforming    │ │
│ │    the S&P 500's 2.1% gain. Your tech holdings (AAPL,    │ │
│ │    MSFT) are the main drivers...                          │ │
│ │    [View Detailed Analysis]                                │ │
│ │                                                             │ │
│ │ 👤 Should I buy more Tesla?                               │ │
│ │                                                             │ │
│ │ 🤖 Based on your current allocation and TSLA's recent     │ │
│ │    performance... [Typing...]                             │ │
│ └─────────────────────────────────────────────────────────────┘ │
│ Message: [Type your question..._______________] [Send] [🎤]    │
└─────────────────────────────────────────────────────────────────┘
```

**Key Components:**
- Chat message history with clear distinction between user and AI
- Message input field with send button
- Optional voice input button
- Clear chat functionality
- AI response with action buttons (View Analysis, etc.)
- Loading states for AI responses

---

## 7. 📈 Reports Page

### Tax Report Generation
```
┌─────────────────────────────────────────────────────────────────┐
│ Tax Reports                                                     │
├─────────────────────────────────────────────────────────────────┤
│ Generate Capital Gains Report                                   │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ Tax Year: [2024      ▼]                                     │ │
│ │ Start Date: [01/01/2024] [📅]                               │ │
│ │ End Date:   [12/31/2024] [📅]                               │ │
│ │ Assets: [All Assets   ▼] (or select specific)              │ │
│ │ Format: [PDF ▼] [CSV] [Both]                                │ │
│ │                                                             │ │
│ │ Preview Summary:                                            │ │
│ │ • Total Gains: $5,234.50                                   │ │
│ │ • Total Losses: -$1,250.00                                 │ │
│ │ • Net Gain: $3,984.50                                      │ │
│ │ • Short-term: $1,200.00                                    │ │
│ │ • Long-term: $2,784.50                                     │ │
│ │                                                             │ │
│ │ [Generate Report]                                           │ │
│ └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## 8. ⚙️ Settings Page

### User Profile & API Keys
```
┌─────────────────────────────────────────────────────────────────┐
│ Settings                                                        │
├─────────────────────────────────────────────────────────────────┤
│ [Profile] [Security] [API Keys] [Preferences] [Data]           │
├─────────────────────────────────────────────────────────────────┤
│ User Profile                                                    │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ Full Name: [John Doe________________]                       │ │
│ │ Email:     [john@example.com________]                       │ │
│ │ Username:  [johndoe_________________]                       │ │
│ │ Timezone:  [UTC-5 Eastern Time  ▼]                        │ │
│ │                                                             │ │
│ │ [Save Changes]                                              │ │
│ └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
│ API Keys                                                        │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ Name           │Created   │Last Used │Status │Actions       │ │
│ │ Production Key │09/01/25  │09/16/25  │Active │[👁] [🗑]     │ │
│ │ Mobile App     │08/15/25  │09/10/25  │Active │[👁] [🗑]     │ │
│ │                                                             │ │
│ │ [+ Create New API Key]                                      │ │
│ └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🔄 Common UI States

### Loading States
```
[Button Loading]: [⟳ Processing...]
[Table Loading]:  [Skeleton rows with gray bars]
[Chart Loading]:  [Pulsing rectangular placeholder]
[Page Loading]:   [Full-screen spinner with logo]
```

### Error States
```
[Form Error]:     Red border + error message below field
[Page Error]:     Centered error message with retry button
[Network Error]:  Banner at top with retry option
[Empty State]:    Icon + message + call-to-action button
```

### Success States
```
[Form Success]:   Green checkmark + success message
[Save Success]:   Toast notification in top-right
[Action Success]: Brief highlight/animation on affected element
```

---

## 🎨 Design System Components

### Buttons
- **Primary**: Blue background, white text
- **Secondary**: Gray border, dark text
- **Danger**: Red background, white text
- **Ghost**: Transparent background, colored text

### Colors
```css
/* Color Palette */
--primary-blue: #3B82F6
--primary-blue-dark: #2563EB
--success-green: #10B981
--warning-orange: #F59E0B
--danger-red: #EF4444
--gray-50: #F9FAFB
--gray-900: #111827
```

### Typography
```css
/* Text Hierarchy */
h1: 2.25rem (36px), font-bold
h2: 1.875rem (30px), font-semibold
h3: 1.5rem (24px), font-semibold
body: 1rem (16px), font-normal
small: 0.875rem (14px), font-normal
```

---

## 📱 Mobile-Specific Considerations

### Navigation
- **Bottom Tab Bar** for primary navigation on mobile
- **Hamburger Menu** for secondary navigation
- **Swipe Gestures** for common actions

### Touch Targets
- Minimum 44px touch targets
- Adequate spacing between interactive elements
- Thumb-friendly placement of primary actions

### Performance
- **Lazy loading** for images and heavy components
- **Virtual scrolling** for long lists
- **Optimistic updates** for better perceived performance

This wireframe specification provides a comprehensive foundation for creating the actual visual wireframes in Figma or similar design tools, ensuring all functionality and user flows are properly considered in the design process.

