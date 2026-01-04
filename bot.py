import discord
from discord.ext import commands, tasks
import requests
from mcstatus import JavaServer
import random

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

API_URL = "https://api.mcscans.fi/public/v1/servers"
ALLOWED_ROLE_ID = enter the role id that has admin powers here

# -------- Functions used in the code --------
def has_required_role(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False

    return any(role.id == ALLOWED_ROLE_ID for role in interaction.user.roles)

def load_blacklist():
    try:
        with open("blacklisted_ips.txt", "r") as f:
            return {line.strip().lower() for line in f if line.strip()}
    except FileNotFoundError:
        return set()

BLACKLISTED_IPS = load_blacklist()

BLACKLIST_FILE = "blacklisted_ips.txt"

def save_blacklist():
    with open(BLACKLIST_FILE, "w") as f:
        for ip in sorted(BLACKLISTED_IPS):
            f.write(ip + "\n")


def filter_blacklisted_servers(servers):
    filtered = []
    for server in servers:
        hostname = server.get("hostname", "").lower()
        if hostname not in BLACKLISTED_IPS:
            filtered.append(server)
    return filtered


def fetch_servers(page=1, **params):
    params["page"] = page
    response = requests.get(API_URL, params=params)
    if response.status_code != 200:
        return []
    return response.json().get("servers", [])

def fetch_total_servers():
    try:
        response = requests.get(API_URL)
        if response.status_code == 200:
            data = response.json()
            return data.get("totalServers", 0)
        return 0
    except:
        return 0

def get_geolocation(ip: str) -> dict:
    try:
        response = requests.get(f"http://ip-api.com/json/{ip}", timeout=5).json()
        return {
            "country": response.get("country", "Unknown"),
            "city": response.get("city", "Unknown")
        }
    except:
        return {"country": "Unknown", "city": "Unknown"}
    
def clean_motd(motd_obj):
    try:
        if hasattr(motd_obj, "raw") and isinstance(motd_obj.raw, dict):
            base = motd_obj.raw.get("text", "")
            extras = motd_obj.raw.get("extra", [])
            text = base + "".join(extras)
            return text.strip()

        if hasattr(motd_obj, "parsed"):
            clean = "".join([str(x) for x in motd_obj.parsed if isinstance(x, str)])
            return clean.strip()

        return str(motd_obj).strip()

    except:
        return "Unknown MOTD"

# -------- Button View for server details with pagination --------
class RandomServerButtons(discord.ui.View):
    def __init__(self, servers):
        super().__init__(timeout=120)
        for index, server in enumerate(servers):
            self.add_item(ServerButton(label=f"Server {index + 1}", server=server))

class PlayerListButton(discord.ui.View):
    def __init__(self, players):
        super().__init__(timeout=30)
        self.players = players

    @discord.ui.button(label="Show Players", style=discord.ButtonStyle.blurple)
    async def show_players(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.players:
            await interaction.response.send_message("No players online or query is not enabled on server.", ephemeral=True)
            return

        player_list = "\n".join(self.players)

        embed = discord.Embed(
            title="Online Players",
            description=player_list,
            color=discord.Color.blue()
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

class ServerInfoButtons(discord.ui.View):
    def __init__(self, servers, page=1, params={}):
        super().__init__(timeout=120)
        self.servers = servers
        self.page = page
        self.params = params
        self.start_index = 0
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        start = self.start_index % 20
        end = start + 5
        for index, server in enumerate(self.servers[start:end]):
            self.add_item(ServerButton(label=f"Server {self.start_index + index + 1}", server=server))
        self.add_item(PageButton(label="Previous", style=discord.ButtonStyle.secondary, direction=-1, view=self))
        self.add_item(PageButton(label="Next", style=discord.ButtonStyle.secondary, direction=1, view=self))

class ServerButton(discord.ui.Button):
    def __init__(self, label, server):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.server = server

    async def callback(self, interaction: discord.Interaction):
        geo = self.server.get("geolocation", {})
        embed = discord.Embed(title="Server Information", color=discord.Color.blue())
        embed.add_field(name="IP", value=self.server.get("hostname"), inline=False)
        embed.add_field(name="Country", value=str(geo.get("country")), inline=True)
        embed.add_field(name="City", value=str(geo.get("city")), inline=True)
        embed.add_field(name="Version", value=str(self.server.get("version")), inline=True)

        auth = self.server.get("authMode", -1)
        auth_map = {
            0: ":cross_mark: Offline (Cracked)",
            1: ":white_check_mark: Online Mode",
            2: ":lock: Whitelisted",
            -1: ":question: Unknown"
        }
        if isinstance(auth, str):
            auth_map.update({
                "Offline": ":cross_mark: Offline (Cracked)",
                "Online": ":white_check_mark: Online Mode",
                "Whitelist": ":lock: Whitelisted"
            })
        auth_display = auth_map.get(auth, ":question: Unknown")
        embed.add_field(name="Authentication", value=auth_display, inline=True)

        embed.add_field(name="Online Players", value=str(self.server.get("playerStats", {}).get("onlinePlayers")), inline=True)
        embed.add_field(name="Max Players", value=str(self.server.get("playerStats", {}).get("maxPlayers")), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

class PageButton(discord.ui.Button):
    def __init__(self, label, style, direction, view):
        super().__init__(label=label, style=style)
        self.direction = direction
        self.view_ref = view

    async def callback(self, interaction: discord.Interaction):
        self.view_ref.start_index += self.direction * 5

        # Going forward past current API page
        if self.view_ref.start_index >= 20:
            self.view_ref.page += 1
            self.view_ref.start_index = 0
            self.view_ref.servers = fetch_servers(
                page=self.view_ref.page, **self.view_ref.params
            )

        # Going backward past current API page
        elif self.view_ref.start_index < 0:
            if self.view_ref.page == 1:
                self.view_ref.start_index = 0
            else:
                self.view_ref.page -= 1
                self.view_ref.start_index = 15
                self.view_ref.servers = fetch_servers(
                    page=self.view_ref.page, **self.view_ref.params
                )

        self.view_ref.update_buttons()

        start = self.view_ref.start_index
        end = start + 5

        embed = discord.Embed(
            title=f"Server Search Results - Page {self.view_ref.page}",
            color=discord.Color.blue()
        )

        for i, server in enumerate(self.view_ref.servers[start:end], start=start + 1):
            ip = server.get("hostname")
            geo = server.get("geolocation", {})
            auth = server.get("authMode", -1)

            auth_map = {
                0: ":cross_mark: Offline (Cracked)",
                1: ":white_check_mark: Online Mode",
                2: ":lock: Whitelisted",
                -1: ":question: Unknown"
            }

            if isinstance(auth, str):
                auth_map.update({
                    "Offline": ":cross_mark: Offline (Cracked)",
                    "Online": ":white_check_mark: Online Mode",
                    "Whitelist": ":lock: Whitelisted"
                })

            auth_display = auth_map.get(auth, ":question: Unknown")

            embed.add_field(
                name=f"Server {i}",
                value=(
                    f"**IP:** {ip}\n"
                    f"**Location:** {geo.get('city','Unknown')}, {geo.get('country','Unknown')}\n"
                    f"**Authentication:** {auth_display}"
                ),
                inline=False
            )

        await interaction.response.edit_message(embed=embed, view=self.view_ref)

# -------- Slash Command with page option --------
@bot.tree.command(name="blacklist_add", description="Add an IP or hostname to the blacklist")
async def blacklist_add(interaction: discord.Interaction, ip: str):

    if not has_required_role(interaction):
        await interaction.response.send_message(
            "❌ You do not have permission to use this command.\nContact the project owner for removal.",
            ephemeral=True
        )
        return

    ip = ip.lower().strip()

    if ip in BLACKLISTED_IPS:
        await interaction.response.send_message(
            f"`{ip}` is already blacklisted.",
            ephemeral=True
        )
        return

    BLACKLISTED_IPS.add(ip)
    save_blacklist()

    await interaction.response.send_message(
        f"✅ `{ip}` has been added to the blacklist.",
        ephemeral=True
    )

@bot.tree.command(name="blacklist_list", description="Show all blacklisted IPs")
async def blacklist_list(interaction: discord.Interaction):

    if not has_required_role(interaction):
        await interaction.response.send_message(
            "❌ You do not have permission to use this command.\nContact the project owner for removal.",
            ephemeral=True
        )
        return

    if not BLACKLISTED_IPS:
        await interaction.response.send_message(
            "The blacklist is currently empty.",
            ephemeral=True
        )
        return

    ips = "\n".join(sorted(BLACKLISTED_IPS))

    embed = discord.Embed(
        title="🚫 Blacklisted Servers",
        description=f"```\n{ips}\n```",
        color=discord.Color.red()
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="blacklist_remove", description="Remove an IP or hostname from the blacklist")
async def blacklist_remove(interaction: discord.Interaction, ip: str):

    if not has_required_role(interaction):
        await interaction.response.send_message(
            "❌ You do not have permission to use this command.\nContact the project owner for removal.",
            ephemeral=True
        )
        return

    ip = ip.lower().strip()

    if ip not in BLACKLISTED_IPS:
        await interaction.response.send_message(
            f"`{ip}` is not in the blacklist.",
            ephemeral=True
        )
        return

    BLACKLISTED_IPS.remove(ip)
    save_blacklist()

    await interaction.response.send_message(
        f"🗑️ `{ip}` has been removed from the blacklist.",
        ephemeral=True
    )


@bot.tree.command(name="server", description="Search Minecraft servers with filters")
@discord.app_commands.describe(
    page="Starting page number to fetch servers from (20 servers per page)",
    edition="Choose Minecraft edition: Java or Bedrock",
    software="Filter by server software (e.g., Paper)",
    version="Filter by Minecraft version (e.g., 1.20.1)",
    sort="Sort by player count or protocol",
    authmode="Choose authentication mode: Online/Offline/Whitelist",
    geo="Enable geographic lookup",
    live="Show only live servers (active past 5 min)"
)
@discord.app_commands.choices(
    edition=[
        discord.app_commands.Choice(name="Java", value="Java"),
        discord.app_commands.Choice(name="Bedrock", value="Bedrock")
    ],
    authmode=[
        discord.app_commands.Choice(name="Online", value="Online"),
        discord.app_commands.Choice(name="Offline", value="Offline"),
        discord.app_commands.Choice(name="Whitelist", value="Whitelist")
    ]
)
async def server_cmd(
    interaction: discord.Interaction,
    page: int = 1,
    edition: discord.app_commands.Choice[str] | None = None,
    software: str | None = None,
    version: str | None = None,
    sort: str | None = None,
    authmode: discord.app_commands.Choice[str] | None = None,
    geo: bool | None = None,
    live: bool | None = None
):
    await interaction.response.defer()

    params = {}
    if edition: params["edition"] = edition.value
    if software: params["software"] = software
    if version: params["version"] = version
    if sort: params["sort"] = sort
    if authmode: params["authMode"] = authmode.value
    if geo is not None: params["geo"] = str(geo).lower()
    if live is not None: params["live"] = str(live).lower()

    servers = fetch_servers(page=page, **params)
    servers = filter_blacklisted_servers(servers)

    if not servers:
        await interaction.followup.send(
            "No servers found (after privacy filtering)."
        )
        return

    view = ServerInfoButtons(servers, page=page, params=params)

    embed = discord.Embed(
        title=f"Server Search Results - Page {page}",
        color=discord.Color.blue()
    )

    for i, server in enumerate(servers[:5], start=1):
        ip = server.get("hostname")
        geo_info = server.get("geolocation", {})
        auth = server.get("authMode", -1)

        auth_map = {
            0: ":cross_mark: Offline (Cracked)",
            1: ":white_check_mark: Online Mode",
            2: ":lock: Whitelisted",
            -1: ":question: Unknown"
        }

        embed.add_field(
            name=f"Server {i}",
            value=(
                f"**IP:** {ip}\n"
                f"**Location:** {geo_info.get('city','Unknown')}, "
                f"{geo_info.get('country','Unknown')}\n"
                f"**Authentication:** {auth_map.get(auth, ':question: Unknown')}"
            ),
            inline=False
        )

    await interaction.followup.send(embed=embed, view=view)


@bot.tree.command(name="stats", description="Show statistics about the Minecraft server database.")
async def stats_cmd(interaction: discord.Interaction):

    await interaction.response.defer()

    total = fetch_total_servers()

    embed = discord.Embed(
        title="Statistics",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="Bot author:",
        value="<@521371256763711489> (@cheezball69420)", 
        inline=False
    )

    embed.add_field(
        name="API:",
        value="https://mcscans.fi/", 
        inline=False
    )

    embed.add_field(
        name="Total Servers:",
        value=f"**{total:,}**",
        inline=False
    )

    await interaction.followup.send(embed=embed)

@bot.tree.command(name="help", description="Show help for commands.")
async def help_cmd(interaction: discord.Interaction):

    embed = discord.Embed(
        title="❓ Help Menu",
        description="List of available commands and how to use them.",
        color=discord.Color.blue()
    )
    server_help = (
        "**/server** — Search for Minecraft servers using the API. If no options chosen gives a random server.\n"
        "**Options:**\n"
        "• **page** — Starting page number to fetch servers (20 servers per page)\n"
        "• **edition** — Choose Minecraft edition: Java or Bedrock\n"
        "• **software** — Filter by server software (e.g., Paper)\n"
        "• **version** — Filter by Minecraft version (e.g., 1.20.1)\n"
        "• **sort** — Sort results by player count or protocol\n"
        "• **authmode** — Authentication mode: Online / Offline / Whitelist\n"
        "• **geo** — Enable geolocation lookup\n"
        "• **live** — Only show live servers (refreshed in the last 5 minutes)\n"
    )
    embed.add_field(
        name="🖥️ /server",
        value=server_help,
        inline=False
    )

    embed.add_field(
        name="🎲 /random",
        value="Gives a list of 5 random servers",
        inline=False
    )

    embed.add_field(
        name="📄 /mcinfo",
        value="Displays information about a server\nUsage: /mcinfo (IP of the server)",
        inline=False
    )

    embed.add_field(
        name="📘 /info",
        value="Credits, Source and Api",
        inline=False
    )

    embed.add_field(
        name="📈 /stats",
        value="Displays statistics about the bot.",
        inline=False
    )
    
    

    embed.set_footer(text="Use the commands with / to start!")

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="mcinfo", description="Get information about a Minecraft Java server.")
async def mcinfo(interaction: discord.Interaction, ip: str):

    await interaction.response.defer()

    if ip.lower().strip() in BLACKLISTED_IPS:
        await interaction.followup.send(
            "🚫 This server has requested removal and cannot be shown.",
            ephemeral=True
        )
        return

    try:
        server = JavaServer.lookup(ip)
        status = server.status()

        try:
            query = server.query()
            players = query.players.names
        except:
            players = []

        geo = get_geolocation(ip)

        embed = discord.Embed(
            title=f"Server Info — {ip}",
            color=discord.Color.blue()
        )

        embed.add_field(name="Status", value="Online", inline=True)
        embed.add_field(name="Version", value=status.version.name, inline=True)
        embed.add_field(
            name="Players",
            value=f"{status.players.online}/{status.players.max}",
            inline=True
        )

        embed.add_field(name="Country", value=geo["country"], inline=True)
        embed.add_field(name="City", value=geo["city"], inline=True)
        embed.add_field(
            name="MOTD",
            value=clean_motd(status.motd) or "Unknown",
            inline=False
        )

        view = PlayerListButton(players)
        await interaction.followup.send(embed=embed, view=view)

    except:
        await interaction.followup.send(
            f"Could not reach `{ip}`.",
            ephemeral=True
        )

@bot.tree.command(name="info", description="Source & Author")
async def info_cmd(interaction: discord.Interaction):

    embed = discord.Embed(
        title="Info and Credits",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="Author",
        value="cheezball69420",
        inline=False
    )

    embed.add_field(
        name="Source Code",
        value="Source code can be found at https://discord.gg/AYbDNEWgHE",
        inline=False
    )

    embed.add_field(
        name="API",
        value="https://mcscans.fi/",
        inline=False
    )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="random", description="Get 5 random Minecraft servers")
async def random_cmd(interaction: discord.Interaction):
    await interaction.response.defer()

    attempts = 0
    servers = []

    while attempts < 5 and len(servers) < 5:
        page = random.randint(1, 5000)
        fetched = fetch_servers(page=page)
        fetched = filter_blacklisted_servers(fetched)
        servers.extend(fetched)
        attempts += 1

    servers = servers[:5]

    if not servers:
        await interaction.followup.send(
            "No servers found (after privacy filtering)."
        )
        return

    view = RandomServerButtons(servers)
    embed = discord.Embed(
        title="Random Server Selection",
        color=discord.Color.blue()
    )

    for i, server in enumerate(servers, start=1):
        ip = server.get("hostname")
        geo_info = server.get("geolocation", {})
        auth = server.get("authMode", -1)

        embed.add_field(
            name=f"Server {i}",
            value=(
                f"**IP:** {ip}\n"
                f"**Location:** {geo_info.get('city','Unknown')}, "
                f"{geo_info.get('country','Unknown')}"
            ),
            inline=False
        )

    await interaction.followup.send(embed=embed, view=view)

# -------- Task to update bot activity every 5 minutes --------
@tasks.loop(minutes=5)
async def update_activity():
    total = fetch_total_servers()
    activity_text = f"{total} Minecraft servers"
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=activity_text))


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        await bot.tree.sync()
        print("Slash commands synced.")
    except Exception as e:
        print(e)
    update_activity.start()

bot.run("bot-token")
