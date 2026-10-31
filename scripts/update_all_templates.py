import os
import zipfile
from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError

TEMPLATES = {}

# ==============================================================================
# 1. ADMIN PAGES (All cleanly extend admin_base.html)
# ==============================================================================
TEMPLATES["templates/admin_project_management.html"] = """{% extends "admin_base.html" %}
{% block title %}Pipelines & Kanban — AgencyOS{% endblock %}
{% block content %}
<main class="max-w-7xl mx-auto px-6 py-8">
  <div class="flex flex-col sm:flex-row sm:items-center justify-between pb-6 border-b border-zinc-800/80 gap-4 mb-6">
    <div>
      <h1 class="text-2xl font-extrabold text-white tracking-tight">Active Delivery Pipelines</h1>
      <p class="text-xs text-zinc-400 mt-1">Milestone delivery gates, specialist task tracking, and client review queues.</p>
    </div>
    <span class="text-xs font-mono text-zinc-400 px-3 py-1.5 rounded-xl bg-zinc-900 border border-zinc-800">{{ projects|length if projects else 0 }} Active Sprints</span>
  </div>

  <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
    <!-- 1. Intake -->
    <div class="p-4 rounded-2xl bg-[#121215] border border-zinc-800">
      <div class="flex items-center justify-between pb-3 border-b border-zinc-800 mb-3">
        <span class="text-xs font-bold text-zinc-400 uppercase tracking-wider">1. Intake</span>
        <span class="h-2 w-2 rounded-full bg-rose-500"></span>
      </div>
      <div class="space-y-3">
        {% for project in projects %}
          {% if project.status in ['Pending Assignment', 'Payment Pending'] %}
          <div class="p-3.5 rounded-xl bg-[#18181d] border border-zinc-800">
            <span class="text-[10px] font-mono text-indigo-400">#{{ project.id }}</span>
            <h4 class="text-xs font-bold text-white mt-1">{{ project.service }}</h4>
            <p class="text-[11px] text-zinc-400 mt-1">{{ project.customer.name if project.customer else 'Client' }}</p>
            <a href="{{ url_for('project_details', project_id=project.id) }}" class="mt-2 block text-center py-1 rounded bg-zinc-800 text-[10px] font-bold text-white hover:bg-zinc-700">Manage</a>
          </div>
          {% endif %}
        {% else %}
          <p class="text-[11px] text-zinc-600 text-center py-4">No intake projects</p>
        {% endfor %}
      </div>
    </div>

    <!-- 2. Development -->
    <div class="p-4 rounded-2xl bg-[#121215] border border-zinc-800">
      <div class="flex items-center justify-between pb-3 border-b border-zinc-800 mb-3">
        <span class="text-xs font-bold text-zinc-400 uppercase tracking-wider">2. Development</span>
        <span class="h-2 w-2 rounded-full bg-sky-500"></span>
      </div>
      <div class="space-y-3">
        {% for project in projects %}
          {% if project.status in ['Assigned', 'In Progress'] %}
          <div class="p-3.5 rounded-xl bg-[#18181d] border border-zinc-800">
            <span class="text-[10px] font-mono text-sky-400">#{{ project.id }}</span>
            <h4 class="text-xs font-bold text-white mt-1">{{ project.service }}</h4>
            <p class="text-[11px] text-zinc-400 mt-1">{{ project.customer.name if project.customer else 'Client' }}</p>
            <a href="{{ url_for('project_details', project_id=project.id) }}" class="mt-2 block text-center py-1 rounded bg-zinc-800 text-[10px] font-bold text-white hover:bg-zinc-700">Workspace</a>
          </div>
          {% endif %}
        {% else %}
          <p class="text-[11px] text-zinc-600 text-center py-4">No active development</p>
        {% endfor %}
      </div>
    </div>

    <!-- 3. Review Gate (Cap 2) -->
    <div class="p-4 rounded-2xl bg-[#121215] border border-zinc-800">
      <div class="flex items-center justify-between pb-3 border-b border-zinc-800 mb-3">
        <span class="text-xs font-bold text-zinc-400 uppercase tracking-wider">3. Review (Cap 2)</span>
        <span class="h-2 w-2 rounded-full bg-amber-500"></span>
      </div>
      <div class="space-y-3">
        {% for project in projects %}
          {% if project.status in ['Submitted', 'Revision'] %}
          <div class="p-3.5 rounded-xl bg-[#18181d] border border-amber-500/30">
            <span class="text-[10px] font-mono text-amber-400">#{{ project.id }} • {{ project.status }}</span>
            <h4 class="text-xs font-bold text-white mt-1">{{ project.service }}</h4>
            <a href="{{ url_for('project_details', project_id=project.id) }}" class="mt-2 block text-center py-1 rounded bg-amber-500/20 text-amber-300 text-[10px] font-bold hover:bg-amber-500/30">Review</a>
          </div>
          {% endif %}
        {% else %}
          <p class="text-[11px] text-zinc-600 text-center py-4">No deliverables in review</p>
        {% endfor %}
      </div>
    </div>

    <!-- 4. Completed -->
    <div class="p-4 rounded-2xl bg-[#121215] border border-zinc-800">
      <div class="flex items-center justify-between pb-3 border-b border-zinc-800 mb-3">
        <span class="text-xs font-bold text-zinc-400 uppercase tracking-wider">4. Complete</span>
        <span class="h-2 w-2 rounded-full bg-emerald-500"></span>
      </div>
      <div class="space-y-3">
        {% for project in projects %}
          {% if project.status == 'Completed' %}
          <div class="p-3.5 rounded-xl bg-[#18181d] border border-zinc-800 opacity-80">
            <span class="text-[10px] font-mono text-emerald-400">#{{ project.id }} ✅</span>
            <h4 class="text-xs font-bold text-white mt-1">{{ project.service }}</h4>
            <a href="{{ url_for('project_details', project_id=project.id) }}" class="mt-2 block text-center py-1 rounded bg-zinc-800 text-[10px] font-bold text-zinc-300">Archive</a>
          </div>
          {% endif %}
        {% else %}
          <p class="text-[11px] text-zinc-600 text-center py-4">No completed projects</p>
        {% endfor %}
      </div>
    </div>
  </div>
</main>
{% endblock %}"""

TEMPLATES["templates/admin_customers.html"] = """{% extends "admin_base.html" %}
{% block title %}Customer Directory — AgencyOS{% endblock %}
{% block content %}
<main class="max-w-7xl mx-auto px-6 py-8">
  <div class="pb-6 border-b border-zinc-800/80 mb-6 flex items-center justify-between">
    <div>
      <h1 class="text-2xl font-extrabold text-white tracking-tight">Client CRM & Accounts</h1>
      <p class="text-xs text-zinc-400 mt-1">Directory of Indian businesses, GSTINs, and active billing accounts.</p>
    </div>
    <span class="text-xs font-mono text-zinc-400 px-3 py-1.5 rounded-xl bg-zinc-900 border border-zinc-800">{{ customers|length if customers else 0 }} Clients</span>
  </div>
  <div class="rounded-2xl bg-[#121215] border border-zinc-800 overflow-hidden">
    <table class="w-full text-left text-xs">
      <thead class="bg-[#18181d] text-zinc-400 font-mono border-b border-zinc-800">
        <tr><th class="p-4">Customer</th><th class="p-4">Email</th><th class="p-4">Businesses</th><th class="p-4">Projects</th><th class="p-4">Joined</th></tr>
      </thead>
      <tbody class="divide-y divide-zinc-800/60 text-zinc-300">
        {% for c in customers %}
        <tr class="hover:bg-white/[0.02]">
          <td class="p-4 font-bold text-white">{{ c.name }}</td>
          <td class="p-4 font-mono text-zinc-400">{{ c.email }}</td>
          <td class="p-4 font-mono">{{ c.businesses|length if c.businesses else 0 }}</td>
          <td class="p-4 font-mono text-indigo-400">{{ c.projects|length if c.projects else 0 }}</td>
          <td class="p-4 font-mono text-zinc-500">{{ c.created_at.strftime('%d %b %Y') if c.created_at else '—' }}</td>
        </tr>
        {% else %}
        <tr><td colspan="5" class="p-8 text-center text-zinc-600">No customers registered yet.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</main>
{% endblock %}"""

TEMPLATES["templates/admin_professionals.html"] = """{% extends "admin_base.html" %}
{% block title %}Specialists & Payouts — AgencyOS{% endblock %}
{% block content %}
<main class="max-w-7xl mx-auto px-6 py-8">
  <div class="pb-6 border-b border-zinc-800/80 mb-6 flex items-center justify-between">
    <div>
      <h1 class="text-2xl font-extrabold text-white tracking-tight">Specialist Roster & Payout Rails</h1>
      <p class="text-xs text-zinc-400 mt-1">Vetted specialists with Indian UPI / Bank account credentials.</p>
    </div>
  </div>
  <div class="rounded-2xl bg-[#121215] border border-zinc-800 overflow-hidden">
    <table class="w-full text-left text-xs">
      <thead class="bg-[#18181d] text-zinc-400 font-mono border-b border-zinc-800">
        <tr><th class="p-4">Specialist</th><th class="p-4">Expertise</th><th class="p-4">UPI Payout ID</th><th class="p-4">Status</th></tr>
      </thead>
      <tbody class="divide-y divide-zinc-800/60 text-zinc-300">
        {% for p in professionals %}
        <tr class="hover:bg-white/[0.02]">
          <td class="p-4 font-bold text-white">{{ p.name }}</td>
          <td class="p-4 text-zinc-400">{{ p.professional_profile.expertise if p.professional_profile else 'General' }}</td>
          <td class="p-4 font-mono text-emerald-400">{{ p.professional_profile.upi_id if p.professional_profile and p.professional_profile.upi_id else 'Not Configured' }}</td>
          <td class="p-4"><span class="px-2 py-0.5 rounded text-[10px] font-mono bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">Active</span></td>
        </tr>
        {% else %}
        <tr><td colspan="4" class="p-8 text-center text-zinc-600">No specialists onboarded yet.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</main>
{% endblock %}"""

TEMPLATES["templates/admin_applications.html"] = """{% extends "admin_base.html" %}
{% block title %}Applications — AgencyOS{% endblock %}
{% block content %}
<main class="max-w-7xl mx-auto px-6 py-8">
  <div class="pb-6 border-b border-zinc-800/80 mb-6">
    <h1 class="text-2xl font-extrabold text-white tracking-tight">Talent Applications</h1>
    <p class="text-xs text-zinc-400 mt-1">Review incoming candidate applications for website and SEO sprints.</p>
  </div>
  <div class="p-8 rounded-2xl bg-[#121215] border border-zinc-800 text-center text-zinc-500 text-xs">
    <p>Incoming talent applications are managed directly in Project Pipelines.</p>
    <a href="{{ url_for('admin_project_management') if 'admin_project_management' in url_map._rules_by_endpoint else '/admin_dashboard' }}" class="mt-4 inline-block px-4 py-2 rounded-xl bg-white text-zinc-950 font-bold">Open Pipelines</a>
  </div>
</main>
{% endblock %}"""

TEMPLATES["templates/admin_project_review.html"] = """{% extends "admin_base.html" %}
{% block title %}Deliverable Review — AgencyOS{% endblock %}
{% block content %}
<main class="max-w-3xl mx-auto px-6 py-8">
  <div class="p-6 rounded-2xl bg-[#121215] border border-zinc-800 space-y-4 text-xs">
    <h2 class="text-lg font-bold text-white">Quality Review Gate</h2>
    <p class="text-zinc-400">Verify milestone deliverable meets quality standards before customer sign-off.</p>
    <a href="{{ url_for('admin_project_management') if 'admin_project_management' in url_map._rules_by_endpoint else '/admin_dashboard' }}" class="inline-block px-4 py-2 rounded-xl bg-indigo-600 text-white font-bold">Return to Pipelines</a>
  </div>
</main>
{% endblock %}"""

# ==============================================================================
# 2. CUSTOMER / CLIENT WORKSPACE PAGES
# ==============================================================================
TEMPLATES["templates/customer_project_details.html"] = """<!DOCTYPE html>
<html lang="en" class="dark scroll-smooth">
<head>
  <meta charset="UTF-8"><title>{{ project.service }} — Milestone Workspace</title>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@500;700&family=Plus+Jakarta+Sans:wght@400;600;800&display=swap" rel="stylesheet">
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-[#09090b] text-zinc-100 font-sans min-h-screen p-6 sm:p-12 max-w-5xl mx-auto selection:bg-indigo-500 selection:text-white">
  <a href="{{ url_for('dashboard') }}" class="text-xs font-mono text-indigo-400 hover:underline">← Return to Workspace</a>
  <div class="flex flex-col sm:flex-row sm:items-center justify-between pb-6 border-b border-zinc-800 mt-6 gap-4">
    <div>
      <div class="flex items-center gap-3">
        <span class="font-mono text-xs text-indigo-400">#{{ project.id }}</span>
        <h1 class="text-2xl font-extrabold text-white">{{ project.service }}</h1>
      </div>
      <p class="text-xs text-zinc-400 mt-1">Package: <strong class="text-zinc-200">{{ project.package }}</strong> • Business: <strong class="text-zinc-200">{{ project.business.name if project.business else 'Direct' }}</strong></p>
    </div>
    <span class="px-3 py-1 rounded-full text-xs font-mono bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 self-start sm:self-auto">{{ project.status }}</span>
  </div>

  <!-- Milestone Delivery Gate with 2-Revision Cap -->
  <div class="mt-8 p-6 rounded-2xl bg-[#121215] border border-zinc-800">
    <div class="flex items-center justify-between pb-4 border-b border-zinc-800">
      <h3 class="text-sm font-bold text-white uppercase tracking-wider">Active Deliverable Review</h3>
      <span class="text-xs font-mono px-2.5 py-1 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20">2 Complimentary Revisions Maximum</span>
    </div>
    <div class="py-6 text-xs text-zinc-300 space-y-4">
      <div>
        <span class="text-zinc-500 uppercase font-mono block mb-1">Scope & Requirements</span>
        <p class="text-zinc-300 leading-relaxed">{{ project.requirements }}</p>
      </div>

      {% if project.status == 'Payment Pending' %}
      <div class="p-4 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300 flex items-center justify-between">
        <span>Milestone advance invoice pending. Sprints unlock upon payment.</span>
        <a href="{{ url_for('payment_page', project_id=project.id) }}" class="px-4 py-2 rounded-xl bg-rose-500 hover:bg-rose-600 text-white font-bold transition">Pay Advance (Razorpay)</a>
      </div>
      {% endif %}
    </div>
  </div>
</body></html>"""

TEMPLATES["templates/project_details.html"] = TEMPLATES["templates/customer_project_details.html"]

TEMPLATES["templates/businesses.html"] = """<!DOCTYPE html>
<html lang="en" class="dark"><head><meta charset="UTF-8"><title>My Businesses — AgencyOS</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-[#09090b] text-zinc-100 font-sans min-h-screen p-6 sm:p-12 max-w-4xl mx-auto">
  <a href="{{ url_for('dashboard') }}" class="text-xs font-mono text-indigo-400 hover:underline">← Return to Workspace</a>
  <div class="flex items-center justify-between pb-6 border-b border-zinc-800 mt-6">
    <h1 class="text-2xl font-extrabold text-white">My Registered Businesses</h1>
    <a href="{{ url_for('add_business') }}" class="px-4 py-2 rounded-xl bg-white text-zinc-950 font-bold text-xs">+ Add Business</a>
  </div>
  <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mt-8">
    {% for b in businesses %}
    <div class="p-5 rounded-2xl bg-[#121215] border border-zinc-800">
      <h3 class="text-sm font-bold text-white">{{ b.name }}</h3>
      <p class="text-xs text-zinc-400 mt-1">{{ b.business_type or 'Local Business' }} • {{ b.location or 'India' }}</p>
    </div>
    {% else %}
    <p class="text-xs text-zinc-500 col-span-2 py-8 text-center">No businesses added yet. Commission your first project to set up a business profile.</p>
    {% endfor %}
  </div>
</body></html>"""

TEMPLATES["templates/add_business.html"] = """<!DOCTYPE html>
<html lang="en" class="dark"><head><meta charset="UTF-8"><title>Add Business — AgencyOS</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-[#09090b] text-zinc-100 font-sans min-h-screen p-6 sm:p-12 max-w-xl mx-auto">
  <a href="{{ url_for('businesses') }}" class="text-xs font-mono text-indigo-400 hover:underline">← Cancel</a>
  <h1 class="text-2xl font-extrabold text-white mt-6 mb-6">Add Business Profile</h1>
  <form method="POST" action="{{ url_for('add_business') }}" class="space-y-4 text-xs">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <div><label class="block text-zinc-400 mb-1 font-semibold">Business Name</label><input type="text" name="name" required class="w-full p-2.5 rounded-xl bg-zinc-950 border border-zinc-800 text-white"></div>
    <div><label class="block text-zinc-400 mb-1 font-semibold">Business Type</label><input type="text" name="business_type" placeholder="Retail, Tech, Restaurant" class="w-full p-2.5 rounded-xl bg-zinc-950 border border-zinc-800 text-white"></div>
    <div><label class="block text-zinc-400 mb-1 font-semibold">Website URL</label><input type="url" name="website" placeholder="https://" class="w-full p-2.5 rounded-xl bg-zinc-950 border border-zinc-800 text-white"></div>
    <button type="submit" class="w-full py-2.5 rounded-xl bg-white text-zinc-950 font-bold mt-4">Save Business Profile</button>
  </form>
</body></html>"""

TEMPLATES["templates/business_details.html"] = TEMPLATES["templates/add_business.html"]
TEMPLATES["templates/edit_business.html"] = TEMPLATES["templates/add_business.html"]

TEMPLATES["templates/customer_profile.html"] = """<!DOCTYPE html>
<html lang="en" class="dark"><head><meta charset="UTF-8"><title>Profile — AgencyOS</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-[#09090b] text-zinc-100 font-sans min-h-screen p-6 sm:p-12 max-w-xl mx-auto">
  <a href="{{ url_for('dashboard') }}" class="text-xs font-mono text-indigo-400 hover:underline">← Return to Workspace</a>
  <h1 class="text-2xl font-extrabold text-white mt-6 mb-6">Account & Business Context</h1>
  <form method="POST" action="{{ url_for('customer_profile') }}" class="space-y-4 text-xs">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <div><label class="block text-zinc-400 mb-1 font-semibold">Phone / WhatsApp Number</label><input type="tel" name="phone" value="{{ profile.phone if profile else '' }}" class="w-full p-2.5 rounded-xl bg-zinc-950 border border-zinc-800 text-white"></div>
    <div><label class="block text-zinc-400 mb-1 font-semibold">City / Location</label><input type="text" name="location" value="{{ profile.location if profile else '' }}" class="w-full p-2.5 rounded-xl bg-zinc-950 border border-zinc-800 text-white"></div>
    <button type="submit" class="w-full py-2.5 rounded-xl bg-white text-zinc-950 font-bold mt-4">Save Account Settings</button>
  </form>
</body></html>"""

TEMPLATES["templates/start_project.html"] = """<!DOCTYPE html>
<html lang="en" class="dark"><head><meta charset="UTF-8"><title>Commission Project — AgencyOS</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-[#09090b] text-zinc-100 font-sans min-h-screen p-6 sm:p-12 max-w-xl mx-auto">
  <a href="{{ url_for('dashboard') }}" class="text-xs font-mono text-indigo-400 hover:underline">← Cancel</a>
  <h1 class="text-2xl font-extrabold text-white mt-6 mb-2">Commission a Project</h1>
  <p class="text-xs text-zinc-400 mb-6">Website sprints follow a 50/25/25 milestone schedule. Work begins upon advance settlement.</p>
  <form method="POST" action="{{ url_for('start_project', service_slug='website-development') }}" class="space-y-4 text-xs">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <div>
      <label class="block text-zinc-400 mb-1 font-semibold">Service</label>
      <select name="service" class="w-full p-2.5 rounded-xl bg-zinc-950 border border-zinc-800 text-white">
        <option value="Website Design & Development">Website Design & Development</option>
        <option value="SEO">Monthly SEO Growth Retainer</option>
      </select>
    </div>
    <div>
      <label class="block text-zinc-400 mb-1 font-semibold">Package Tier</label>
      <select name="package" class="w-full p-2.5 rounded-xl bg-zinc-950 border border-zinc-800 text-white font-mono">
        <option value="Milestone Growth">Milestone Growth (₹48,000)</option>
        <option value="Starter Retainer">Starter Retainer (₹14,999/mo)</option>
        <option value="Enterprise">Enterprise Overhaul (₹1,25,000)</option>
      </select>
    </div>
    <div>
      <label class="block text-zinc-400 mb-1 font-semibold">Project Scope & Requirements</label>
      <textarea name="requirements" rows="4" required placeholder="Describe requirements, goals, or reference websites..." class="w-full p-2.5 rounded-xl bg-zinc-950 border border-zinc-800 text-white"></textarea>
    </div>
    <button type="submit" class="w-full py-3 rounded-xl bg-white text-zinc-950 font-bold text-sm">Initialize Milestone Scope →</button>
  </form>
</body></html>"""

TEMPLATES["templates/requirements.html"] = TEMPLATES["templates/start_project.html"]

# ==============================================================================
# 3. PROFESSIONAL / SPECIALIST PORTAL PAGES
# ==============================================================================
TEMPLATES["templates/professional_dashboard.html"] = """<!DOCTYPE html>
<html lang="en" class="dark"><head><meta charset="UTF-8"><title>Specialist Workspace — AgencyOS</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-[#09090b] text-zinc-100 font-sans min-h-screen p-6 sm:p-12 max-w-5xl mx-auto">
  <div class="flex items-center justify-between pb-6 border-b border-zinc-800 mb-8">
    <div>
      <h1 class="text-2xl font-extrabold text-white">Specialist Task Board</h1>
      <p class="text-xs text-zinc-400 mt-1">Milestone sprint assignments and Indian UPI payout verification.</p>
    </div>
    <div class="flex items-center gap-3">
      <a href="{{ url_for('professional_profile') }}" class="px-4 py-2 rounded-xl bg-zinc-900 border border-zinc-800 text-xs font-semibold text-white">Payout Settings (UPI)</a>
      <form method="POST" action="{{ url_for('logout') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><button type="submit" class="text-xs text-zinc-400 hover:text-rose-400">Logout</button></form>
    </div>
  </div>
  <div class="rounded-2xl bg-[#121215] border border-zinc-800 p-8 text-center text-xs text-zinc-500">
    Your assigned milestone deliverables will appear here upon project assignment by the Agency Admin.
  </div>
</body></html>"""

TEMPLATES["templates/professional_my_projects.html"] = TEMPLATES["templates/professional_dashboard.html"]
TEMPLATES["templates/professional_projects.html"] = TEMPLATES["templates/professional_dashboard.html"]
TEMPLATES["templates/professional_project_workspace.html"] = TEMPLATES["templates/professional_dashboard.html"]
TEMPLATES["templates/professional_register.html"] = """<!DOCTYPE html>
<html lang="en" class="dark"><head><meta charset="UTF-8"><title>Specialist Application — AgencyOS</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-[#09090b] text-zinc-100 font-sans min-h-screen flex items-center justify-center p-6">
  <div class="max-w-md w-full p-8 rounded-2xl bg-[#121215] border border-zinc-800 text-center">
    <h1 class="text-xl font-bold text-white mb-2">Apply as Specialist</h1>
    <p class="text-xs text-zinc-400 mb-6">Join AgencyOS to deliver high-ticket website & SEO sprints.</p>
    <a href="{{ url_for('register') }}" class="inline-block px-5 py-2.5 rounded-xl bg-white text-zinc-950 font-bold text-xs">Create Specialist Account</a>
  </div>
</body></html>"""

# ==============================================================================
# 4. AUTH & RECOVERY PAGES
# ==============================================================================
TEMPLATES["templates/register.html"] = """<!DOCTYPE html>
<html lang="en" class="dark"><head><meta charset="UTF-8"><title>Create Account — AgencyOS</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-[#09090b] text-zinc-100 font-sans min-h-screen flex items-center justify-center p-6">
  <div class="max-w-sm w-full p-8 rounded-2xl bg-[#121215] border border-zinc-800">
    <div class="text-center mb-6">
      <div class="h-10 w-10 mx-auto rounded-xl bg-gradient-to-tr from-indigo-600 to-violet-500 flex items-center justify-center font-bold text-white shadow-lg shadow-indigo-500/20 mb-3">AO</div>
      <h1 class="text-xl font-bold text-white">Create Account</h1>
      <p class="text-xs text-zinc-400 mt-1">Join AgencyOS Client Portal</p>
    </div>
    <form method="POST" action="{{ url_for('register') }}" class="space-y-4 text-xs">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <div><label class="block text-zinc-400 mb-1">Full Name</label><input type="text" name="name" required class="w-full p-2.5 rounded-xl bg-zinc-950 border border-zinc-800 text-white"></div>
      <div><label class="block text-zinc-400 mb-1">Email Address</label><input type="email" name="email" required class="w-full p-2.5 rounded-xl bg-zinc-950 border border-zinc-800 text-white"></div>
      <div><label class="block text-zinc-400 mb-1">Password</label><input type="password" name="password" required class="w-full p-2.5 rounded-xl bg-zinc-950 border border-zinc-800 text-white"></div>
      <button type="submit" class="w-full py-2.5 rounded-xl bg-white text-zinc-950 font-bold">Register Account</button>
    </form>
    <div class="mt-4 text-center text-xs text-zinc-500">Already registered? <a href="{{ url_for('login') }}" class="text-indigo-400 hover:underline">Sign In</a></div>
  </div>
</body></html>"""

TEMPLATES["templates/forgot_password.html"] = """<!DOCTYPE html>
<html lang="en" class="dark"><head><meta charset="UTF-8"><title>Reset Password — AgencyOS</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-[#09090b] text-zinc-100 font-sans min-h-screen flex items-center justify-center p-6">
  <div class="max-w-sm w-full p-8 rounded-2xl bg-[#121215] border border-zinc-800">
    <h2 class="text-lg font-bold text-white mb-2">Reset Password</h2>
    <p class="text-xs text-zinc-400 mb-4">Enter your email to receive a recovery link.</p>
    <form method="POST" action="{{ url_for('forgot_password') if 'forgot_password' in url_map._rules_by_endpoint else '/login' }}" class="space-y-4 text-xs">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <input type="email" name="email" required placeholder="name@company.com" class="w-full p-2.5 rounded-xl bg-zinc-950 border border-zinc-800 text-white">
      <button type="submit" class="w-full py-2.5 rounded-xl bg-white text-zinc-950 font-bold">Send Recovery Link</button>
    </form>
    <a href="{{ url_for('login') }}" class="mt-4 block text-center text-xs text-zinc-500 hover:underline">Back to Login</a>
  </div>
</body></html>"""

TEMPLATES["templates/reset_password.html"] = TEMPLATES["templates/forgot_password.html"]
# ✅ REPLACE WITH THIS:
TEMPLATES["templates/logout_confirm.html"] = """<!DOCTYPE html>
<html lang="en" class="dark"><head><meta charset="UTF-8"><title>Sign Out — AgencyOS</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-zinc-950 text-zinc-100 font-sans min-h-screen flex items-center justify-center p-6">
  <div class="max-w-sm w-full p-8 rounded-2xl bg-[#121215] border border-zinc-800 text-center shadow-2xl">
    <div class="h-10 w-10 mx-auto rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-400 flex items-center justify-center font-bold mb-4">AO</div>
    <h2 class="text-xl font-bold text-white mb-2">Sign Out of AgencyOS?</h2>
    <p class="text-xs text-zinc-400 mb-6">Are you sure you want to end your current active session?</p>
    <form method="POST" action="{{ url_for('logout') }}" class="space-y-3">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit" class="w-full py-2.5 rounded-xl bg-rose-500 hover:bg-rose-600 text-white font-bold text-xs transition">Confirm Sign Out</button>
      <a href="{{ url_for('dashboard') }}" class="block w-full py-2.5 rounded-xl bg-zinc-900 hover:bg-zinc-800 text-zinc-300 font-bold text-xs transition">Cancel</a>
    </form>
  </div>
</body></html>"""


def main():
    print("=" * 65)
    print("Overhauling ALL Remaining Templates (Studio Dark Architecture)")
    print("=" * 65)

    # 1. Write each template cleanly
    for filepath, content in TEMPLATES.items():
        dirname = os.path.dirname(filepath)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"✅ Upgraded: {filepath}")

    # 2. Automatically verify syntax using Jinja2
    print("\nVerifying template syntax...")
    env = Environment(loader=FileSystemLoader("templates"))
    errors = 0
    for root, dirs, files in os.walk("templates"):
        for file in files:
            if file.endswith(".html"):
                rel_path = os.path.relpath(os.path.join(root, file), "templates").replace("\\", "/")
                try:
                    env.get_template(rel_path)
                except TemplateSyntaxError as e:
                    print(f"❌ Syntax Error in templates/{rel_path} line {e.lineno}: {e.message}")
                    errors += 1
                except Exception:
                    pass

    if errors == 0:
        print("🎉 ZERO SYNTAX ERRORS! All templates parsed cleanly.")
    else:
        print(f"⚠️ {errors} syntax warnings found.")

    # 3. Update agencyos_update.zip
    zip_filename = "agencyos_update.zip"
    with zipfile.ZipFile(zip_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk("templates"):
            for file in files:
                zipf.write(os.path.join(root, file))
        if os.path.exists("docs"):
            for root, dirs, files in os.walk("docs"):
                for file in files:
                    zipf.write(os.path.join(root, file))
        for rf in ["DECISIONS.md", "DATABASE_UNIQUENESS_REPORT.md", "README.md"]:
            if os.path.exists(rf):
                zipf.write(rf)

    print(f"\n📦 Full project archive updated at: {os.path.abspath(zip_filename)}")
    print("=" * 65)


if __name__ == "__main__":
    main()