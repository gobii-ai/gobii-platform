"""
Example data for the home page agent templates.
"""

# Simple examples that appear as quick buttons
SIMPLE_EXAMPLES = [
    {
        "label": "Alert me when Tesla stock drops 5% for buying opportunity",
        "prompt": "Monitor Tesla (TSLA) stock price and alert me immediately when it drops 5% or more from current price for a potential buying opportunity"
    },
    {
        "label": "Find Reddit leads mentioning 'CRM software' daily",
        "prompt": "Search Reddit daily for posts mentioning 'CRM software', 'customer management', or 'sales automation' and alert me to potential leads for my SaaS business"
    },
    {
        "label": "Track Amazon competitors undercutting my prices",
        "prompt": "Monitor my top 3 competitors on Amazon for price changes and alert me within 1 hour if they undercut my product prices so I can adjust pricing strategy"
    },
    {
        "label": "Alert me when Nvidia earnings beat AMD by 10%+",
        "prompt": "Monitor quarterly earnings reports for Nvidia vs AMD and alert me when Nvidia beats AMD earnings by 10% or more for portfolio rebalancing"
    },
    {
        "label": "Alert me when Nintendo Switch 2 is back in stock at Best Buy",
        "prompt": "Check Best Buy, Walmart, Target, and GameStop every 5 minutes for Nintendo Switch 2 availability and text me immediately when it's in stock with the direct purchase link and current price"
    },
    {
        "label": "Monitor severe weather alerts for my kids' school district",
        "prompt": "Track National Weather Service alerts for tornado warnings, severe thunderstorm warnings, and school closure announcements for Austin ISD and text me immediately with safety instructions"
    },
    {
        "label": "Find Airpods Pro deals under $180 at any major retailer",
        "prompt": "Monitor Amazon, Best Buy, Target, Costco, and Apple Store for AirPods Pro 2nd generation price drops below $180 and alert me with the link when found"
    },
    {
        "label": "Track my kids' school bus delays and cancellations",
        "prompt": "Monitor Austin ISD transportation alerts, weather-related delays, and route cancellations for bus #4251 and text me 15 minutes before pickup time with any changes"
    },
    {
        "label": "Alert me when my competitor gets bad reviews on Google",
        "prompt": "Monitor Google Reviews for my top 3 local competitors and alert me when they receive 1-2 star reviews so I can capitalize on their service issues with targeted marketing"
    },
    {
        "label": "Find concert tickets under $100 for my favorite artists",
        "prompt": "Monitor Ticketmaster, StubHub, and SeatGeek for Taylor Swift, The Weeknd, and Bad Bunny tickets under $100 in Austin, Dallas, and Houston venues with instant purchase alerts"
    },
    {
        "label": "Track when my Airbnb competitors drop their prices",
        "prompt": "Monitor similar 3BR properties within 2 miles of my Austin Airbnb and alert me when competitors drop prices below $150/night so I can adjust my pricing strategy"
    },
    {
        "label": "Alert me when crypto whales buy Bitcoin above $10M",
        "prompt": "Monitor blockchain transactions for Bitcoin purchases over $10 million and alert me with wallet addresses, transaction amounts, and market impact analysis for trading decisions"
    },
]

# Rich examples with detailed cards
RICH_EXAMPLES = [
    {
        "id": "financial_monitoring",
        "title": "Smart Portfolio Management",
        "subtitle": "AI-Powered Investment Tracking",
        "description": "Get real-time alerts when Apple hits $200, Tesla drops 8%, or when Nvidia earnings exceed analyst estimates by 15%. Includes insider trading alerts and sector rotation signals for your portfolio.",
        "icon": "M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z",
        "gradient": "from-green-500 to-emerald-600",
        "prompt": "Monitor my tech portfolio: Alert me when Apple (AAPL) hits $200, Tesla (TSLA) drops 8%, Nvidia (NVDA) earnings exceed estimates by 15%, or when any insider sells >$10M shares. Include weekly sector rotation analysis and crypto correlation alerts for Bitcoin above $100k."
    },
    {
        "id": "lead_generation",
        "title": "Reddit Lead Generation",
        "subtitle": "Convert Social Conversations to Sales",
        "description": "Find high-intent buyers on Reddit discussing problems your product solves. Target subreddits like r/Entrepreneur, r/SaaS, r/marketing with AI-generated helpful responses that mention your solution naturally.",
        "icon": "M7 8h10M7 12h4m1 8l-4-4H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-3l-4 4z",
        "gradient": "from-purple-500 to-pink-600",
        "prompt": "Monitor Reddit for potential customers discussing 'project management software', 'team collaboration tools', or 'CRM alternatives' in r/Entrepreneur, r/SaaS, r/startups. Alert me to high-intent posts with 10+ upvotes and draft helpful responses that naturally mention our solution. Focus on users spending $500+/month on current tools."
    },
    {
        "id": "competitive_intelligence",
        "title": "Competitor Price Monitoring",
        "subtitle": "Stay Ahead of Market Changes",
        "description": "Track when Salesforce cuts enterprise pricing by 20%, HubSpot launches new features, or Zoom increases meeting participant limits. Get 1-hour alerts to adjust your pricing and marketing strategy.",
        "icon": "M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z",
        "gradient": "from-orange-500 to-red-600",
        "prompt": "Monitor Salesforce, HubSpot, and Monday.com for pricing changes, new feature launches, and customer reviews below 3 stars. Alert me within 1 hour when Salesforce cuts enterprise pricing >15%, HubSpot launches marketing automation features, or competitors get negative reviews about reliability issues. Include weekly market share analysis."
    },
    {
        "id": "ecommerce_monitoring",
        "title": "Amazon FBA Optimization",
        "subtitle": "Inventory & Profit Maximization",
        "description": "Monitor when your main product drops out of stock on Amazon, competitors undercut your price by $5+, or when reviews fall below 4.2 stars. Get Buy Box alerts and restock notifications to maximize sales.",
        "icon": "M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4",
        "gradient": "from-blue-500 to-indigo-600",
        "prompt": "Monitor my Amazon listings: Alert when wireless earbuds inventory drops below 50 units, competitors undercut my $49.99 price by $5+, or customer reviews fall below 4.2 stars. Track Buy Box percentage and notify when I lose it for >2 hours. Include daily competitor analysis for top 5 similar products in Electronics > Headphones."
    },
    {
        "id": "social_media_monitoring",
        "title": "Brand Crisis Prevention",
        "subtitle": "Real-Time Reputation Management",
        "description": "Get instant alerts when TechCrunch mentions your company, negative reviews spike on Glassdoor, or competitors announce funding rounds >$10M. Includes sentiment analysis and response templates.",
        "icon": "M15 17h5l-5 5v-5zM4.027 12.97l-.743-.743m16.673 0l-.743.743m-8.927 8.283l-.743-.743m0-16.673l.743-.743m-3.785 8.786h-2.5m17 0h-2.5m-8.5-3.5v-2.5m0 17v-2.5",
        "gradient": "from-teal-500 to-cyan-600",
        "prompt": "Monitor for brand mentions: Alert me immediately when TechCrunch, VentureBeat, or Product Hunt mentions our company 'CloudSync Inc', when Glassdoor reviews drop below 4.0, or Twitter mentions exceed 100/hour. Track competitor funding announcements >$10M and negative sentiment spikes about our customer service on Reddit or LinkedIn."
    },
    {
        "id": "supply_chain_optimization",
        "title": "Supply Chain Intelligence",
        "subtitle": "Vendor & Logistics Monitoring",
        "description": "Track when your key supplier's delivery times exceed 14 days, freight costs spike 25%, or manufacturing delays hit your top 3 product lines. Get weather alerts affecting shipping routes and backup vendor recommendations.",
        "icon": "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z",
        "gradient": "from-violet-500 to-purple-600",
        "prompt": "Monitor supply chain for our manufacturing business: Alert when semiconductor supplier delivery times exceed 14 days, freight costs from China increase >25%, or production delays hit our top 3 product lines. Track weather affecting Port of Los Angeles and recommend backup suppliers when primary vendors show delivery risks. Include monthly vendor performance analysis."
    },
    {
        "id": "gaming_console_hunter",
        "title": "Gaming Console Hunter",
        "subtitle": "Never Miss Hot Product Restocks",
        "description": "Track PlayStation 5, Nintendo Switch 2, RTX 4090 graphics cards, and iPhone 16 Pro availability across all major retailers. Get instant text alerts with direct purchase links the moment they're in stock.",
        "icon": "M11 4a2 2 0 114 0v1a1 1 0 001 1h3a1 1 0 011 1v3a1 1 0 01-1 1h-1a2 2 0 100 4h1a1 1 0 011 1v3a1 1 0 01-1 1h-3a1 1 0 01-1-1v-1a2 2 0 10-4 0v1a1 1 0 01-1 1H7a1 1 0 01-1-1v-3a1 1 0 011-1h1a2 2 0 100-4H7a1 1 0 01-1-1V7a1 1 0 011-1h3a1 1 0 001-1V4z",
        "gradient": "from-indigo-500 to-purple-600",
        "prompt": "Hunt for Nintendo Switch 2 restocks at Best Buy, Walmart, Target, GameStop, and Amazon. Text me instantly when in stock with direct purchase links. Also track PlayStation 5 Pro, RTX 4090 graphics cards, iPhone 16 Pro Max 1TB, and Steam Deck OLED availability. Include current prices and compare against MSRP."
    },
    {
        "id": "weather_family_safety",
        "title": "Family Safety Weather Guard",
        "subtitle": "Protect Your Loved Ones 24/7",
        "description": "Monitor National Weather Service alerts for tornado warnings, flash flood warnings, and severe thunderstorms in your area. Get instant alerts with safety instructions when your kids' schools are affected or dangerous weather approaches your home.",
        "icon": "M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586l-2 2V5H5v14h7v2H4a1 1 0 01-1-1V4z M16 10l6 6-6 6v-4H8v-4h8v-4z",
        "gradient": "from-yellow-500 to-orange-600",
        "prompt": "Monitor National Weather Service for tornado warnings, severe thunderstorm warnings, flash flood warnings, and winter storm warnings for Austin, Texas 78701. Alert me immediately when my kids' schools (Austin ISD) issue weather-related closures or when any warning affects my home area. Include safety instructions and recommended actions for each alert type."
    },
    {
        "id": "smart_deal_hunter",
        "title": "Smart Deal Hunter",
        "subtitle": "Catch Every Sale & Price Drop",
        "description": "Track AirPods Pro under $180, iPhone deals below $800, MacBook Air discounts over 15%, and your Amazon wishlist items when they hit target prices. Never pay full price for tech again.",
        "icon": "M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z",
        "gradient": "from-pink-500 to-rose-600",
        "prompt": "Monitor deals: Alert when AirPods Pro 2nd Gen drops below $180, iPhone 15 Pro under $800, MacBook Air M3 has 15%+ discount, Samsung 65' OLED TV under $1,500, and Dyson V15 vacuum under $400. Check Best Buy, Amazon, Target, Costco, and Apple Store. Include coupon codes and cashback opportunities when available."
    },
    {
        "id": "home_security_guardian",
        "title": "Home Security Guardian",
        "subtitle": "Neighborhood Safety Intelligence",
        "description": "Monitor police reports, Nextdoor crime alerts, and local news for break-ins, car thefts, and suspicious activity within 2 miles of your home. Get real-time safety updates with crime prevention tips.",
        "icon": "M12 1l3 3h4v4l3 3-3 3v4h-4l-3 3-3-3H5v-4l-3-3 3-3V5h4l3-3z",
        "gradient": "from-cyan-500 to-blue-600",
        "prompt": "Monitor local crime reports, Nextdoor neighborhood alerts, Ring camera reports, and Austin Police Department incidents within 2 miles of my home at 78704. Alert me about break-ins, car thefts, package theft, suspicious activity, and safety concerns. Include crime prevention tips and recommend security upgrades when patterns emerge."
    },
    {
        "id": "flight_deal_tracker",
        "title": "Flight Deal Tracker",
        "subtitle": "Never Pay Full Price to Travel",
        "description": "Track round-trip flights from your home airport to dream destinations. Get alerts when Austin to Tokyo drops below $800, or domestic flights hit your price targets. Includes hotel and car rental deals too.",
        "icon": "M21 16v-2l-8-5V3.5c0-.83-.67-1.5-1.5-1.5S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z",
        "gradient": "from-emerald-500 to-teal-600",
        "prompt": "Track flight deals from Austin (AUS) to Tokyo under $800, London under $500, Hawaii under $400, New York under $200, and Los Angeles under $150. Monitor Delta, American, United, Southwest, and Google Flights. Alert me for round-trip deals departing within next 6 months. Include hotel deals under $100/night and rental car discounts at destination cities."
    },
    {
        "id": "health_appointment_finder",
        "title": "Health Appointment Finder",
        "subtitle": "Skip the Waiting Lists",
        "description": "Monitor cancellations for dermatologist appointments, dentist cleanings, specialist visits, and urgent care availability near you. Get alerted when someone cancels so you can grab their slot.",
        "icon": "M19 14l-7 7m0 0l-7-7m7 7V3",
        "gradient": "from-red-500 to-pink-600",
        "prompt": "Monitor appointment cancellations for dermatologist visits within 30 days in Austin, TX, dentist cleaning appointments within 2 weeks, cardiologist consultations within 60 days, and same-day urgent care availability at top-rated clinics. Alert me immediately when slots open up with provider name, appointment time, and booking instructions."
    },
    {
        "id": "rental_apartment_scout",
        "title": "Dream Apartment Scout",
        "subtitle": "Find Your Perfect Home First",
        "description": "Track new apartment listings under $2,000/month, 2+ bedrooms, pet-friendly, with parking in your target neighborhoods. Get alerts with photos, virtual tour links, and application instructions before they hit the market.",
        "icon": "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6",
        "gradient": "from-amber-500 to-yellow-600",
        "prompt": "Monitor new apartment listings under $2,000/month, 2+ bedrooms, pet-friendly with dog park nearby, parking included, in South Austin, East Austin, or Downtown Austin. Track Zillow, Apartments.com, Craigslist, and local property management sites. Alert me with photos, virtual tour links, walkability scores, and application deadlines before listings go viral."
    },
    {
        "id": "crypto_whale_tracker",
        "title": "Crypto Whale Tracker",
        "subtitle": "Follow the Smart Money",
        "description": "Track large Bitcoin transactions over $10M, Ethereum whale movements, and smart contract interactions by top DeFi protocols. Get alerts when whales accumulate or dump major positions with market impact analysis.",
        "icon": "M12 2l3.09 6.26L22 9l-5.91 3.74L18 19l-6-3-6 3 1.91-6.26L2 9l6.91-.74L12 2z",
        "gradient": "from-yellow-400 to-orange-500",
        "prompt": "Monitor Bitcoin transactions over $10 million, Ethereum whale wallets with >10k ETH, and major smart contract interactions on Uniswap, Aave, and Compound. Alert me when whales accumulate or dump positions, with analysis of potential market impact and correlation to price movements. Include DeFi yield farming opportunities above 15% APY."
    },
    {
        "id": "sports_betting_edge",
        "title": "Sports Betting Edge",
        "subtitle": "Find Value in Live Odds",
        "description": "Track odds movements across DraftKings, FanDuel, and BetMGM for NBA, NFL, and Premier League games. Get alerts when line movements indicate sharp money or when arbitrage opportunities appear with 3%+ profit margins.",
        "icon": "M13 10V3L4 14h7v7l9-11h-7z",
        "gradient": "from-green-400 to-blue-500",
        "prompt": "Monitor real-time odds for NBA, NFL, and Premier League across DraftKings, FanDuel, BetMGM, and Caesars. Alert me when line movements exceed 3 points, reverse line movement occurs, or arbitrage opportunities appear with 3%+ profit. Include injury reports, weather impacts for outdoor games, and sharp vs public money percentages."
    },
    {
        "id": "local_business_spy",
        "title": "Local Business Intelligence",
        "subtitle": "Outperform Your Competition",
        "description": "Monitor your local competitors' Google reviews, social media posts, new service offerings, and promotional campaigns. Get alerts when they receive bad reviews, change pricing, or launch new marketing initiatives you can counter.",
        "icon": "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z",
        "gradient": "from-purple-400 to-indigo-500",
        "prompt": "Monitor my 5 main local competitors in Austin: track their Google Reviews for ratings below 3 stars, Facebook/Instagram posts about new services or promotions, website changes to pricing or service offerings, and job postings indicating expansion. Alert me within 2 hours of negative reviews so I can target affected customers, and when they launch campaigns I can counter-program against."
    },
    {
        "id": "restaurant_reservation_hunter",
        "title": "Restaurant Reservation Hunter",
        "subtitle": "Never Miss a Great Table",
        "description": "Track cancellations at impossible-to-book restaurants like Uchi, Suerte, and Le Bernardin. Get instant alerts when prime-time slots open up with direct booking links and automatic reservation attempts.",
        "icon": "M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z",
        "gradient": "from-orange-400 to-red-500",
        "prompt": "Monitor restaurant availability for Uchi Austin, Suerte, Le Bernardin NYC, and The French Laundry for party of 2-4 between 7-9pm. Alert me immediately when cancellations occur with direct OpenTable/Resy booking links. Also track new restaurant openings, James Beard Award announcements, and Michelin star updates in Austin, NYC, and SF food scenes."
    },
    {
        "id": "influencer_collaboration_finder",
        "title": "Influencer Collaboration Finder",
        "subtitle": "Scale Your Brand Partnerships",
        "description": "Find micro-influencers in your niche with 10k-100k followers, high engagement rates, and brand-safe content. Get alerts when they post about competitors or relevant topics, with outreach templates and contact information.",
        "icon": "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z",
        "gradient": "from-pink-400 to-purple-500",
        "prompt": "Find micro-influencers (10k-100k followers) in fitness, beauty, and tech niches with engagement rates >4%, brand-safe content, and authentic audiences. Alert me when they post about competitors, relevant hashtags, or show interest in brand partnerships. Include contact emails, rate cards, and automated outreach templates for collaboration proposals."
    },
] 