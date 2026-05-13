# bot.py - BANIDA STORE (Completo com Botão Copiar PIX)
import discord
from discord.ext import commands
from discord import Embed, PartialEmoji
import aiohttp
import mercadopago
import uuid
import asyncio
import os
import asyncpg
import secrets
import random
import string
import subprocess
import tempfile
import shutil
import io
import re
from datetime import datetime, timedelta
from aiohttp import web

# ================= CONFIGURAÇÃO =================
DISCORD_TOKEN   = os.getenv("LOJA_DISCORD_TOKEN")
MP_TOKEN        = os.getenv("MERCADO_PAGO_TOKEN")
DATABASE_URL    = os.getenv("DATABASE_URL")
GUILD_ID        = os.getenv("GUILD_ID")
CARGO_DONO      = os.getenv("CARGO_DONO")
CANAL_LOJA      = os.getenv("CANAL_LOJA")
CANAL_VENDAS    = os.getenv("CANAL_VENDAS")
CANAL_LOG_VENDAS = os.getenv("CANAL_LOG_VENDAS")
CANAL_LOG_ADMIN  = os.getenv("CANAL_LOG_ADMIN")
CANAL_TICKET_PANEL = os.getenv("CANAL_TICKET_PANEL", "1504161545662496768")
CATEGORIA_TICKETS = os.getenv("CATEGORIA_TICKETS", "1504164472502091796")
CANAL_PAINEL_ADMIN = os.getenv("CANAL_PAINEL_ADMIN", "1504170405953273979")

missing = []
for name, val in [
    ("LOJA_DISCORD_TOKEN", DISCORD_TOKEN),
    ("MERCADO_PAGO_TOKEN", MP_TOKEN),
    ("DATABASE_URL", DATABASE_URL),
    ("GUILD_ID", GUILD_ID),
    ("CARGO_DONO", CARGO_DONO),
    ("CANAL_LOJA", CANAL_LOJA),
    ("CANAL_VENDAS", CANAL_VENDAS)
]:
    if not val:
        missing.append(name)
if missing:
    print("❌ Variáveis obrigatórias não configuradas:", ", ".join(missing))
    exit(1)

GUILD_ID        = int(GUILD_ID)
CARGO_DONO      = int(CARGO_DONO)
CANAL_LOJA      = int(CANAL_LOJA)
CANAL_VENDAS    = int(CANAL_VENDAS)
CANAL_TICKET_PANEL = int(CANAL_TICKET_PANEL)
CATEGORIA_TICKETS = int(CATEGORIA_TICKETS)
CANAL_PAINEL_ADMIN = int(CANAL_PAINEL_ADMIN)
if CANAL_LOG_VENDAS:
    CANAL_LOG_VENDAS = int(CANAL_LOG_VENDAS)
if CANAL_LOG_ADMIN:
    CANAL_LOG_ADMIN = int(CANAL_LOG_ADMIN)

if "railwaypostgresql://" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("railwaypostgresql://", "postgresql://")

COR_PRINCIPAL   = 0xFF69B4
COR_SUCESSO     = 0xFF1493
COR_ERRO        = 0x8B0000
COR_PENDENTE    = 0xFFB6C1
COR_DESTAQUE    = 0xFF69B4

sdk = mercadopago.SDK(MP_TOKEN)
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

db = None
pedidos_pendentes = {}
tickets_ativos = {}

# ================= HELPERS =================
def gerar_id():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))

def gerar_senha_arquivo():
    return ''.join(random.choices(string.ascii_uppercase + string.ascii_lowercase + string.digits, k=32))

def formatar_preco(v):
    return f"R$ {float(v):.2f}".replace(".", ",")

def verificar_7zip():
    return shutil.which("7z") is not None

def instalar_7zip():
    try:
        subprocess.run(["apt-get", "update"], capture_output=True, text=True, timeout=60)
        result = subprocess.run(
            ["apt-get", "install", "-y", "p7zip-full"],
            capture_output=True, text=True, timeout=120
        )
        return result.returncode == 0
    except:
        return False

def criar_embed(titulo="", descricao="", cor=COR_PRINCIPAL):
    embed = Embed(title=titulo, description=descricao, color=cor)
    embed.set_footer(text="🌸 BANIDA STORE")
    embed.timestamp = datetime.utcnow()
    return embed

async def get_guild():
    return bot.get_guild(GUILD_ID)

def parse_emoji(emoji_str: str):
    if not emoji_str:
        return None
    match = re.match(r'<(a?):(\w+):(\d+)>', emoji_str)
    if match:
        animated = match.group(1) == 'a'
        name = match.group(2)
        emoji_id = int(match.group(3))
        return PartialEmoji(animated=animated, name=name, id=emoji_id)
    return emoji_str

# ================= BANCO DE DADOS =================
async def init_db():
    global db
    try:
        db = await asyncpg.create_pool(DATABASE_URL)
        async with db.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS produtos (
                    id TEXT PRIMARY KEY, nome TEXT NOT NULL, preco REAL NOT NULL,
                    emoji TEXT DEFAULT '🛒', descricao TEXT DEFAULT '',
                    arquivo_nome TEXT DEFAULT NULL, arquivo_data BYTEA DEFAULT NULL
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pedidos (
                    id TEXT PRIMARY KEY, user_id BIGINT NOT NULL, produto_id TEXT NOT NULL,
                    produto_nome TEXT NOT NULL, produto_preco REAL NOT NULL,
                    status TEXT DEFAULT 'pendente', criado_em TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS vendas (
                    id SERIAL PRIMARY KEY, total REAL DEFAULT 0, quantidade INTEGER DEFAULT 0
                )
            """)
            await conn.execute("INSERT INTO vendas (id,total,quantidade) VALUES (1,0,0) ON CONFLICT (id) DO NOTHING")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS vendas_realizadas (
                    id SERIAL PRIMARY KEY, pedido_id TEXT NOT NULL, user_id BIGINT NOT NULL,
                    produto_nome TEXT NOT NULL, valor REAL NOT NULL, criado_em TIMESTAMP DEFAULT NOW()
                )
            """)
        print("✅ Banco conectado!")
        return True
    except Exception as e:
        print(f"❌ Erro banco: {e}")
        return False

async def get_produtos():
    async with db.acquire() as conn:
        rows = await conn.fetch("SELECT id,nome,preco,emoji,descricao,arquivo_nome FROM produtos")
        return {r["id"]: dict(r) for r in rows}

async def get_produto_completo(pid):
    async with db.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM produtos WHERE id=$1", pid)

async def add_produto(pid, nome, preco, emoji, descricao=""):
    async with db.acquire() as conn:
        await conn.execute("INSERT INTO produtos VALUES ($1,$2,$3,$4,$5,NULL,NULL)", pid, nome, preco, emoji, descricao)

async def edit_produto(pid, nome, preco, emoji, descricao):
    async with db.acquire() as conn:
        await conn.execute("UPDATE produtos SET nome=$2,preco=$3,emoji=$4,descricao=$5 WHERE id=$1", pid, nome, preco, emoji, descricao)

async def salvar_arquivo_produto(pid, nome_arquivo, dados: bytes):
    async with db.acquire() as conn:
        await conn.execute("UPDATE produtos SET arquivo_nome=$2, arquivo_data=$3 WHERE id=$1", pid, nome_arquivo, dados)

async def remover_arquivo_produto(pid):
    async with db.acquire() as conn:
        await conn.execute("UPDATE produtos SET arquivo_nome=NULL, arquivo_data=NULL WHERE id=$1", pid)

async def remove_produto(pid):
    async with db.acquire() as conn:
        await conn.execute("DELETE FROM produtos WHERE id=$1", pid)

async def add_pedido(pid, user_id, produto_id, nome, preco):
    async with db.acquire() as conn:
        await conn.execute("INSERT INTO pedidos VALUES ($1,$2,$3,$4,$5,'pendente',NOW())", pid, user_id, produto_id, nome, preco)

async def update_pedido(pid, status):
    async with db.acquire() as conn:
        await conn.execute("UPDATE pedidos SET status=$1 WHERE id=$2", status, pid)

async def get_vendas():
    async with db.acquire() as conn:
        r = await conn.fetchrow("SELECT total,quantidade FROM vendas WHERE id=1")
        return r["total"], r["quantidade"]

async def add_venda(valor):
    async with db.acquire() as conn:
        await conn.execute("UPDATE vendas SET total=total+$1, quantidade=quantidade+1 WHERE id=1", valor)

async def registrar_venda_realizada(pedido_id, user_id, produto_nome, valor):
    async with db.acquire() as conn:
        await conn.execute("INSERT INTO vendas_realizadas (pedido_id,user_id,produto_nome,valor) VALUES ($1,$2,$3,$4)", pedido_id, user_id, produto_nome, valor)

async def limpar_banco_completo():
    async with db.acquire() as conn:
        await conn.execute("DELETE FROM vendas_realizadas")
        await conn.execute("DELETE FROM pedidos")
        await conn.execute("DELETE FROM produtos")
        await conn.execute("UPDATE vendas SET total=0, quantidade=0 WHERE id=1")

# ================= LOGS =================
async def log_venda(pedido_id, user, produto, valor, senha_arquivo=None):
    if not CANAL_LOG_VENDAS:
        return
    canal = bot.get_channel(CANAL_LOG_VENDAS)
    if not canal: return
    embed = criar_embed(titulo="🌸 VENDA FINALIZADA", descricao="Nova compra aprovada!", cor=COR_SUCESSO)
    embed.add_field(name="🆔 Pedido", value=f"`{pedido_id}`", inline=True)
    embed.add_field(name="👤 Comprador", value=f"<@{user.id}> ({user.name})", inline=True)
    embed.add_field(name="📦 Produto", value=produto, inline=True)
    embed.add_field(name="💰 Valor", value=formatar_preco(valor), inline=True)
    embed.add_field(name="🔐 Senha", value=f"`{senha_arquivo}`" if senha_arquivo else "Sem arquivo", inline=False)
    await canal.send(embed=embed)

async def log_admin(acao, admin, detalhes, cor=COR_DESTAQUE):
    if not CANAL_LOG_ADMIN:
        return
    canal = bot.get_channel(CANAL_LOG_ADMIN)
    if not canal: return
    embed = criar_embed(titulo=f"⚙️ ADMIN • {acao}", descricao=detalhes, cor=cor)
    embed.add_field(name="👑 Admin", value=f"<@{admin.id}> ({admin.name})", inline=True)
    await canal.send(embed=embed)

# ================= SISTEMA DE TICKETS =================
class TicketSelect(discord.ui.Select):
    def __init__(self, user):
        self.user = user
        options = [
            discord.SelectOption(label="📖 Tirar dúvidas sobre conteúdo", value="duvidas", emoji="📖", description="Esclareça suas dúvidas sobre os produtos"),
            discord.SelectOption(label="🛒 Compras", value="compras", emoji="🛒", description="Problemas ou informações sobre sua compra")
        ]
        super().__init__(placeholder="Selecione o motivo do ticket...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tipo = self.values[0]
        asyncio.create_task(self.criar_ticket(interaction, tipo))

    async def criar_ticket(self, interaction: discord.Interaction, tipo: str):
        try:
            guild = interaction.guild
            user = self.user
            categoria = guild.get_channel(CATEGORIA_TICKETS)
            if not categoria:
                await interaction.followup.send(f"❌ Categoria não encontrada.", ephemeral=True)
                return

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
                user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True)
            }
            cargo_admin = guild.get_role(CARGO_DONO)
            if cargo_admin:
                overwrites[cargo_admin] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True)

            prefixo = "duvidas" if tipo == "duvidas" else "compras"
            nome_canal = f"{prefixo}-{user.name.lower().replace(' ', '-')[:15]}"
            canal = await guild.create_text_channel(name=nome_canal, category=categoria, overwrites=overwrites)

            tickets_ativos[user.id] = canal.id

            titulo = "📖 Dúvidas sobre Conteúdo" if tipo == "duvidas" else "🛒 Atendimento de Compras"
            descricao = "Em breve um atendente irá esclarecer suas dúvidas." if tipo == "duvidas" else "Informe o número do seu pedido ou detalhes da compra."
            embed = discord.Embed(
                title=f"🌸 BANIDA STORE - {titulo}",
                description=f"{descricao}\n\n{user.mention}, descreva sua solicitação.\n\n**Para fechar, use o botão abaixo.**",
                color=COR_PRINCIPAL
            )
            embed.set_footer(text=f"Ticket de {tipo} • Aguarde")
            embed.timestamp = datetime.utcnow()

            view = FecharTicketView(user.id, canal.id)
            await canal.send(embed=embed, view=view)
            await canal.send(user.mention)

            await log_admin("Ticket Aberto", user, f"Canal `{canal.name}` - Tipo: {tipo}")
            await interaction.followup.send(f"✅ Ticket criado! Acesse: {canal.mention}", ephemeral=True)
        except Exception as e:
            print(f"Erro ao criar ticket: {e}")
            await interaction.followup.send(f"❌ Erro: {str(e)[:100]}", ephemeral=True)

class AbrirTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 Abrir Ticket", style=discord.ButtonStyle.primary, emoji="🎫")
    async def abrir_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        user = interaction.user

        if user.id in tickets_ativos:
            canal_existente = bot.get_channel(tickets_ativos[user.id])
            if canal_existente:
                await interaction.followup.send(f"❌ Você já possui um ticket aberto: {canal_existente.mention}", ephemeral=True)
                return
            else:
                del tickets_ativos[user.id]

        view = discord.ui.View()
        view.add_item(TicketSelect(user))
        await interaction.followup.send("📌 **Selecione o motivo do atendimento:**", view=view, ephemeral=True)

class FecharTicketView(discord.ui.View):
    def __init__(self, user_id, channel_id):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.channel_id = channel_id

    @discord.ui.button(label="🔒 Fechar Ticket", style=discord.ButtonStyle.danger, emoji="🔒")
    async def fechar_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if interaction.user.id != self.user_id and not any(r.id == CARGO_DONO for r in interaction.user.roles):
            await interaction.followup.send("❌ Sem permissão.", ephemeral=True)
            return

        embed = discord.Embed(title="🔒 Fechar Ticket", description="Tem certeza? O canal será excluído.", color=COR_ERRO)
        view = ConfirmarFechamentoView(self.channel_id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

class ConfirmarFechamentoView(discord.ui.View):
    def __init__(self, channel_id):
        super().__init__(timeout=30)
        self.channel_id = channel_id

    @discord.ui.button(label="✅ Sim, fechar", style=discord.ButtonStyle.danger)
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        canal = bot.get_channel(self.channel_id)
        if canal:
            for uid, cid in list(tickets_ativos.items()):
                if cid == self.channel_id:
                    del tickets_ativos[uid]
                    break
            await log_admin("Ticket Fechado", interaction.user, f"Canal `{canal.name}` foi excluído.")
            await canal.delete(reason="Ticket fechado")
        else:
            await interaction.followup.send("❌ Canal não encontrado.", ephemeral=True)

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ Fechamento cancelado.", ephemeral=True)

# ================= LOJA (MODAIS, SELECTS, VIEWS) =================
class ProdutoModal(discord.ui.Modal, title="✨ Adicionar Produto"):
    nome_input = discord.ui.TextInput(label="📦 Nome", placeholder="Ex: VIP Rosa", required=True)
    preco_input = discord.ui.TextInput(label="💰 Preço", placeholder="49.90", required=True)
    emoji_input = discord.ui.TextInput(label="😀 Emoji", placeholder="👑 ou <a:exemplo:ID>", required=False, default="🛒")
    descricao_input = discord.ui.TextInput(label="📝 Descrição", placeholder="Breve descrição", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            pid = gerar_id()
            nome = self.nome_input.value
            preco = float(self.preco_input.value.replace(",", "."))
            emoji = self.emoji_input.value or "🛒"
            descricao = self.descricao_input.value or ""
            produtos = await get_produtos()
            while pid in produtos: pid = gerar_id()
            await add_produto(pid, nome, preco, emoji, descricao)
            embed = criar_embed(titulo="✅ Produto Adicionado!", cor=COR_SUCESSO)
            embed.add_field(name="🆔 ID", value=f"`{pid}`", inline=True)
            embed.add_field(name="📦 Nome", value=nome, inline=True)
            embed.add_field(name="💰 Preço", value=formatar_preco(preco), inline=True)
            embed.add_field(name="📂 Vincular arquivo", value=f"`!upload {pid}`", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            await atualizar_loja()
            await log_admin("Produto Adicionado", interaction.user, f"**{nome}** • {formatar_preco(preco)} • ID `{pid}`")
        except Exception as e:
            await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)

class EditarProdutoModal(discord.ui.Modal, title="✏️ Editar Produto"):
    def __init__(self, produto):
        super().__init__()
        self.produto_id = produto["id"]
        self.add_item(discord.ui.TextInput(label="📦 Nome", default=produto["nome"], required=True))
        self.add_item(discord.ui.TextInput(label="💰 Preço", default=str(produto["preco"]), required=True))
        self.add_item(discord.ui.TextInput(label="😀 Emoji", default=produto["emoji"], required=False))
        self.add_item(discord.ui.TextInput(label="📝 Descrição", default=produto.get("descricao",""), required=False))

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            nome = self.children[0].value
            preco = float(self.children[1].value.replace(",", "."))
            emoji = self.children[2].value or "🛒"
            descricao = self.children[3].value or ""
            await edit_produto(self.produto_id, nome, preco, emoji, descricao)
            await interaction.followup.send("✅ Produto editado!", ephemeral=True)
            await atualizar_loja()
            await log_admin("Produto Editado", interaction.user, f"**{nome}** • {formatar_preco(preco)} • ID `{self.produto_id}`")
        except Exception as e:
            await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)

class RemoverSelect(discord.ui.Select):
    def __init__(self, produtos):
        options = []
        for pid, p in produtos.items():
            emoji = parse_emoji(p['emoji'])
            options.append(discord.SelectOption(label=f"{p['nome']} ({pid})", value=pid, emoji=emoji))
        super().__init__(placeholder="🗑️ Selecione o produto para remover", options=options[:25])
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        produtos = await get_produtos()
        nome = produtos.get(self.values[0], {}).get("nome", self.values[0])
        await remove_produto(self.values[0])
        await interaction.followup.send(f"✅ **{nome}** removido!", ephemeral=True)
        await atualizar_loja()
        await log_admin("Produto Removido", interaction.user, f"**{nome}** • ID `{self.values[0]}`", cor=COR_ERRO)

class EditarSelect(discord.ui.Select):
    def __init__(self, produtos):
        options = []
        for pid, p in produtos.items():
            emoji = parse_emoji(p['emoji'])
            options.append(discord.SelectOption(label=f"{p['nome']} — {formatar_preco(p['preco'])}", value=pid, emoji=emoji))
        super().__init__(placeholder="✏️ Selecione o produto para editar", options=options[:25])
    async def callback(self, interaction: discord.Interaction):
        produtos = await get_produtos()
        produto = produtos.get(self.values[0])
        if not produto:
            return await interaction.response.send_message("❌ Produto não encontrado.", ephemeral=True)
        await interaction.response.send_modal(EditarProdutoModal(produto))

class ProdutoSelect(discord.ui.Select):
    def __init__(self, produtos):
        options = []
        for pid, p in produtos.items():
            emoji = parse_emoji(p['emoji'])
            options.append(discord.SelectOption(label=f"{p['nome']} — {formatar_preco(p['preco'])}", value=pid, emoji=emoji))
        super().__init__(placeholder="🛒 Escolha um produto", options=options[:25])
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        produto_id = self.values[0]
        produtos = await get_produtos()
        produto = produtos.get(produto_id)
        if not produto:
            return await interaction.followup.send("❌ Produto não encontrado.", ephemeral=True)
        await interaction.followup.send(f"✅ **{produto['emoji']} {produto['nome']}** selecionado! Gerando Pix...", ephemeral=True)
        await iniciar_pagamento(interaction, produto_id)

class ConfirmacaoLimpezaView(discord.ui.View):
    def __init__(self, interaction_original):
        super().__init__(timeout=60)
        self.interaction_original = interaction_original

    @discord.ui.button(label="✅ CONFIRMAR LIMPEZA", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.interaction_original.user.id:
            return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await limpar_banco_completo()
        await atualizar_loja()
        await atualizar_vendas()
        await log_admin("🗑️ Banco Limpo", interaction.user, "Todos os dados foram zerados.", cor=COR_ERRO)
        embed = criar_embed(titulo="✅ Banco de Dados Limpo", descricao="Tudo foi removido.", cor=COR_SUCESSO)
        await self.interaction_original.edit_original_response(embed=embed, view=None)
        await interaction.followup.send("✅ Limpo!", ephemeral=True)

    @discord.ui.button(label="❌ CANCELAR", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.interaction_original.user.id:
            return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        embed = criar_embed(titulo="❌ Cancelado", descricao="Banco intacto.", cor=COR_DESTAQUE)
        await self.interaction_original.edit_original_response(embed=embed, view=None)
        await interaction.followup.send("✅ Cancelado.", ephemeral=True)

class LojaButtons(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="💰 Comprar", style=discord.ButtonStyle.success, emoji="🛒")
    async def comprar(self, interaction: discord.Interaction, button: discord.ui.Button):
        produtos = await get_produtos()
        if not produtos:
            return await interaction.response.send_message("❌ Nenhum produto disponível.", ephemeral=True)
        view = discord.ui.View()
        view.add_item(ProdutoSelect(produtos))
        await interaction.response.send_message("📦 **Selecione o produto:**", view=view, ephemeral=True)

    @discord.ui.button(label="👑 Admin", style=discord.ButtonStyle.danger, emoji="⚙️")
    async def admin(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not any(r.id == CARGO_DONO for r in interaction.user.roles):
            return await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
        embed = criar_embed(titulo="⚙️ Painel Admin", cor=COR_ERRO)
        embed.add_field(name="➕ Adicionar", value="Cadastra produto", inline=True)
        embed.add_field(name="✏️ Editar", value="Altera produto", inline=True)
        embed.add_field(name="🗑️ Remover", value="Remove produto", inline=True)
        embed.add_field(name="📂 Ver Arquivos", value="Arquivos do banco", inline=True)
        embed.add_field(name="🧹 Limpar Banco", value="Limpa tudo", inline=True)
        embed.add_field(name="🧪 Teste de Entrega", value="Envia `banida_teste.txt`", inline=True)
        embed.add_field(name="📊 Estatísticas", value="Faturamento", inline=True)
        embed.add_field(name="📂 Upload", value="`!upload <id>`", inline=True)
        embed.add_field(name="🔧 Instalar 7-Zip", value="`!instalar7z`", inline=True)
        await interaction.response.send_message(embed=embed, view=AdminView(), ephemeral=True)

class AdminView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="➕ Adicionar", style=discord.ButtonStyle.success, row=0)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ProdutoModal())

    @discord.ui.button(label="✏️ Editar", style=discord.ButtonStyle.primary, row=0)
    async def editar(self, interaction: discord.Interaction, button: discord.ui.Button):
        produtos = await get_produtos()
        if not produtos:
            return await interaction.response.send_message("❌ Nenhum produto.", ephemeral=True)
        view = discord.ui.View()
        view.add_item(EditarSelect(produtos))
        await interaction.response.send_message("✏️ Selecione o produto:", view=view, ephemeral=True)

    @discord.ui.button(label="🗑️ Remover", style=discord.ButtonStyle.danger, row=0)
    async def remover(self, interaction: discord.Interaction, button: discord.ui.Button):
        produtos = await get_produtos()
        if not produtos:
            return await interaction.response.send_message("❌ Nenhum produto.", ephemeral=True)
        view = discord.ui.View()
        view.add_item(RemoverSelect(produtos))
        await interaction.response.send_message("🗑️ Selecione o produto:", view=view, ephemeral=True)

    @discord.ui.button(label="📂 Ver Arquivos", style=discord.ButtonStyle.secondary, row=1)
    async def ver_arquivos(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        async with db.acquire() as conn:
            rows = await conn.fetch("SELECT id, nome, arquivo_nome, LENGTH(arquivo_data) as tamanho_bytes FROM produtos WHERE arquivo_data IS NOT NULL")
        if not rows:
            embed = criar_embed(titulo="📂 Arquivos", descricao="*Nenhum arquivo vinculado.*", cor=COR_DESTAQUE)
            return await interaction.followup.send(embed=embed, ephemeral=True)
        embed = criar_embed(titulo="📂 Arquivos no Banco", descricao=f"{len(rows)} arquivo(s):", cor=COR_DESTAQUE)
        total = 0
        for row in rows:
            mb = row["tamanho_bytes"]/1024/1024
            total += row["tamanho_bytes"]
            embed.add_field(name=f"📦 {row['nome']} (`{row['id']}`)", value=f"📄 `{row['arquivo_nome']}`\n📏 {mb:.2f} MB", inline=False)
        embed.add_field(name="📊 Total", value=f"**{len(rows)}** arquivos • **{total/1024/1024:.2f} MB**", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="🧹 Limpar Banco", style=discord.ButtonStyle.danger, row=1)
    async def limpar_banco(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = criar_embed(titulo="⚠️ CONFIRMAÇÃO", descricao="**IRREVERSÍVEL!** Apagará tudo.", cor=COR_ERRO)
        view = ConfirmacaoLimpezaView(interaction)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="🧪 Teste de Entrega", style=discord.ButtonStyle.secondary, row=1)
    async def teste(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild or await get_guild()
        if not guild:
            return await interaction.followup.send("❌ Servidor não encontrado.", ephemeral=True)
        conteudo = b"Arquivo de teste da Banida Store.\nSe voce esta vendo isso, a entrega funcionou!\nKey de uso unico - Canal expira em 5 minutos."
        produto_teste = {"id":"teste","nome":"Produto de Teste","preco":0.0,"emoji":"🧪"}
        pedido_id = f"TESTE-{uuid.uuid4().hex[:8]}"
        await interaction.followup.send("⏳ Criando canal de teste (5 min)...", ephemeral=True)
        await entregar_produto(interaction.user, produto_teste, pedido_id, guild, dados_arquivo_override=conteudo, nome_arquivo_override="banida_teste.txt")
        await interaction.edit_original_response(content="✅ Canal de teste criado! Expira em 5 minutos.")

    @discord.ui.button(label="📊 Estatísticas", style=discord.ButtonStyle.secondary, row=2)
    async def stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        total, qtd = await get_vendas()
        embed = criar_embed(titulo="📊 ESTATÍSTICAS — BANIDA STORE", cor=COR_DESTAQUE)
        embed.add_field(name="📦 Vendas", value=f"**{qtd}** pedidos", inline=True)
        embed.add_field(name="💰 Faturamento", value=f"**{formatar_preco(total)}**", inline=True)
        embed.add_field(name="📈 Ticket Médio", value=formatar_preco(total/qtd) if qtd else "R$ 0,00", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="📖 Tutorial", style=discord.ButtonStyle.primary, emoji="📖", row=2)
    async def tutorial(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = criar_embed(
            titulo="📖 TUTORIAL — PAINEL ADMINISTRATIVO",
            descricao=(
                "Bem-vinda ao painel da **BANIDA STORE**! Veja abaixo como usar cada função.\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            ),
            cor=COR_DESTAQUE
        )
        embed.add_field(name="➕ ADICIONAR PRODUTO", value="Clique em **➕ Adicionar** e preencha o formulário.\nApós salvar, anote o **ID gerado** para vincular arquivo.", inline=False)
        embed.add_field(name="📎 VINCULAR ARQUIVO", value="Use o comando: `!upload <id_do_produto>` com o arquivo anexado.\nTamanho máximo: **25 MB**.", inline=False)
        embed.add_field(name="✏️ EDITAR PRODUTO", value="Clique em **✏️ Editar**, selecione o produto e altere os campos.", inline=False)
        embed.add_field(name="🗑️ REMOVER PRODUTO", value="Clique em **🗑️ Remover** e selecione o produto.", inline=False)
        embed.add_field(name="📂 VER ARQUIVOS", value="Lista todos os produtos que possuem arquivo vinculado.", inline=False)
        embed.add_field(name="🧪 TESTE DE ENTREGA", value="Simula uma compra aprovada enviando um arquivo de teste.", inline=False)
        embed.add_field(name="📊 ESTATÍSTICAS", value="Exibe o total de vendas, faturamento e ticket médio.", inline=False)
        embed.add_field(name="🧹 LIMPAR BANCO", value="⛔ **CUIDADO — IRREVERSÍVEL!** Remove todos os dados.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ================= CRIPTOGRAFIA E ENTREGA =================
def _criar_7z_sync(dados: bytes, nome_original: str, senha: str) -> bytes:
    tmp = tempfile.mkdtemp(prefix="banida_")
    try:
        caminho_original = os.path.join(tmp, nome_original)
        with open(caminho_original, "wb") as f:
            f.write(dados)
        caminho_saida = os.path.join(tmp, "entrega.7z")
        subprocess.run(["7z", "a", f"-p{senha}", "-mhe=on", "-mx=0", caminho_saida, caminho_original],
                       capture_output=True, text=True, timeout=120, check=True)
        with open(caminho_saida, "rb") as f:
            return f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

async def criar_7z_criptografado(dados: bytes, nome_original: str, senha: str) -> bytes:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _criar_7z_sync, dados, nome_original, senha)

async def entregar_produto(user, produto: dict, pedido_id: str, guild, dados_arquivo_override: bytes = None, nome_arquivo_override: str = None):
    senha_arquivo = None
    tem_arquivo = False
    dados_raw = None
    nome_original = None

    if dados_arquivo_override is not None:
        tem_arquivo = True
        senha_arquivo = gerar_senha_arquivo()
        dados_raw = dados_arquivo_override
        nome_original = nome_arquivo_override or "arquivo_banida"
    else:
        prod_completo = await get_produto_completo(produto["id"])
        if prod_completo and prod_completo["arquivo_data"] is not None:
            tem_arquivo = True
            senha_arquivo = gerar_senha_arquivo()
            dados_raw = bytes(prod_completo["arquivo_data"])
            nome_original = prod_completo["arquivo_nome"] or f"produto_{produto['id']}"

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    cargo_dono = guild.get_role(CARGO_DONO)
    if cargo_dono:
        overwrites[cargo_dono] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    nome_canal = f"🛒-compra-{user.name.lower().replace(' ', '-')[:20]}"
    try:
        canal_temp = await guild.create_text_channel(name=nome_canal, overwrites=overwrites, reason=f"Entrega para {user.name}")
    except Exception as e:
        await log_admin("Erro Entrega", user, f"Não foi possível criar canal de entrega: {e}")
        return

    embed = discord.Embed(title="🌸 BANIDA STORE — COMPRA APROVADA",
                          description=f"> Olá, **{user.display_name}**! Pagamento confirmado.\n> ⚠️ **Este canal será excluído em 5 minutos!**\n> 🔐 **Key de uso único.**",
                          color=0xFF69B4)
    embed.set_thumbnail(url=user.display_avatar.url if user.display_avatar else None)
    embed.add_field(name="**📦 Produto**", value=f"{produto['emoji']} {produto['nome']}", inline=True)
    embed.add_field(name="**💳 Valor**", value=f"`{formatar_preco(produto['preco'])}`", inline=True)
    embed.add_field(name="**🆔 Pedido**", value=f"`{pedido_id}`", inline=True)

    if tem_arquivo:
        embed.add_field(name="**🔐 Senha**", value=f"```\n{senha_arquivo}\n```", inline=False)
        embed.set_footer(text="🌸 BANIDA STORE • 5 min para baixar")
        embed.timestamp = datetime.utcnow()
        if not verificar_7zip():
            await canal_temp.send("⚠️ 7-Zip não instalado. Use `!instalar7z`.")
        else:
            dados_cifrados = await criar_7z_criptografado(dados_raw, nome_original, senha_arquivo)
            nome_saida = f"banida_{produto['id']}_{pedido_id[:8]}.7z"
            arquivo = discord.File(fp=io.BytesIO(dados_cifrados), filename=nome_saida)
            await canal_temp.send(embed=embed, file=arquivo)
    else:
        await canal_temp.send(embed=embed)

    async def remover_canal():
        await asyncio.sleep(300)
        try:
            await canal_temp.delete()
        except:
            pass
    asyncio.create_task(remover_canal())

    if not pedido_id.startswith("TESTE-"):
        await registrar_venda_realizada(pedido_id, user.id, produto["nome"], produto["preco"])
        await log_venda(pedido_id, user, produto["nome"], produto["preco"], senha_arquivo)
    else:
        await log_admin("Teste de Entrega", user, f"Pedido `{pedido_id}` | Produto: {produto['nome']}")

# ================= PAGAMENTO COM BOTÃO COPIAR PIX =================
class PixView(discord.ui.View):
    def __init__(self, codigo_pix, payment_id, produto, user, guild):
        super().__init__(timeout=300)
        self.codigo_pix = codigo_pix
        self.payment_id = payment_id
        self.produto = produto
        self.user = user
        self.guild = guild

    @discord.ui.button(label="📋 Copiar PIX", style=discord.ButtonStyle.secondary, emoji="📋")
    async def copy_pix(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"```\n{self.codigo_pix}\n```\n✅ **Código PIX copiado!**\nAgora cole no seu banco/app de pagamento.",
            ephemeral=True
        )

    @discord.ui.button(label="✅ JÁ PAGUEI", style=discord.ButtonStyle.success, emoji="✅")
    async def check_payment(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            info = sdk.payment().get(self.payment_id)
            status = info["response"].get("status")
            if status == "approved":
                await interaction.followup.send("✅ Pagamento aprovado! Canal de entrega criado em 5 minutos.", ephemeral=True)
            elif status == "pending":
                await interaction.followup.send("⏳ Pagamento ainda pendente. Aguarde a confirmação do banco.", ephemeral=True)
            elif status == "rejected":
                await interaction.followup.send("❌ Pagamento recusado. Tente novamente.", ephemeral=True)
            else:
                await interaction.followup.send(f"ℹ️ Status atual: `{status}`", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao verificar: {e}", ephemeral=True)

    @discord.ui.button(label="❌ CANCELAR", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel_payment(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("❌ Pedido cancelado. O PIX não será mais válido.", ephemeral=True)

async def iniciar_pagamento(interaction: discord.Interaction, produto_id: str):
    produtos = await get_produtos()
    produto = produtos.get(produto_id)
    if not produto:
        return await interaction.followup.send("❌ Produto não encontrado.", ephemeral=True)

    # Formata o preço corretamente
    try:
        preco_str = str(produto["preco"]).replace(",", ".")
        valor = float(preco_str)
        
        if valor <= 0:
            return await interaction.followup.send(f"❌ Valor inválido: **{formatar_preco(valor)}**. O preço deve ser maior que zero.", ephemeral=True)
        
        if valor < 0.01:
            return await interaction.followup.send(f"❌ Valor **{formatar_preco(valor)}** é muito baixo. O mínimo é R$ 0,01.", ephemeral=True)
        
        valor = round(valor, 2)
        
    except Exception as e:
        return await interaction.followup.send(f"❌ Erro ao processar o valor do produto: `{produto['preco']}`\nErro: {e}", ephemeral=True)

    print(f"[PAGAMENTO] Produto: {produto['nome']} | Valor: {valor}")

    try:
        payment_data = {
            "transaction_amount": valor,
            "description": f"{produto['nome']} - Banida Store",
            "payment_method_id": "pix",
            "payer": {
                "email": f"banida_{interaction.user.id}@banidastore.com.br",
                "first_name": (interaction.user.name or "Cliente")[:50],
                "identification": {"type": "CPF", "number": "00000000000"}
            },
            "statement_descriptor": "BANIDA STORE"
        }

        payment = sdk.payment().create(payment_data)
        resp = payment["response"]

        if payment.get("status") and int(payment.get("status")) >= 400:
            erro_msg = resp.get("message") or resp.get("error") or str(resp)
            print(f"[MP ERRO {payment.get('status')}] {erro_msg}")
            
            if "transaction_amount" in erro_msg.lower():
                return await interaction.followup.send(
                    f"❌ **Erro ao gerar PIX: valor inválido.**\n"
                    f"> Produto: **{produto['nome']}**\n"
                    f"> Valor: `{formatar_preco(valor)}`\n"
                    f"> Motivo: `{erro_msg[:150]}`",
                    ephemeral=True
                )
            return await interaction.followup.send(f"❌ Erro MP: `{erro_msg[:200]}`", ephemeral=True)

        pix_data = resp.get("point_of_interaction", {}).get("transaction_data", {})
        pix = pix_data.get("qr_code")
        if not pix:
            print(f"[MP] Resposta sem qr_code: {resp}")
            return await interaction.followup.send(
                "❌ Erro ao gerar o PIX: resposta inválida do Mercado Pago.\n"
                "> Verifique se sua conta MP tem o PIX habilitado.",
                ephemeral=True
            )

        pay_id = resp["id"]
        pedido_id = str(uuid.uuid4())
        await add_pedido(pedido_id, interaction.user.id, produto_id, produto["nome"], produto["preco"])
        pedidos_pendentes[pay_id] = pedido_id

        # Remove aspas do PIX (se houver)
        pix_limpo = pix.strip('"\'')

        embed = criar_embed(titulo="💳 PAGAMENTO VIA PIX",
                            descricao=f"**{produto['emoji']} {produto['nome']}**\n💰 **{formatar_preco(valor)}**",
                            cor=COR_PENDENTE)
        
        embed.add_field(name="📋 Código PIX", value=f"```\n{pix_limpo}\n```", inline=False)
        embed.add_field(name="⏰ Validade", value="**30 minutos**", inline=True)
        embed.add_field(name="🏢 Destinatário", value="**BANIDA STORE**", inline=True)
        embed.add_field(name="📱 Como Pagar", 
                        value="1️⃣ Clique no botão **📋 Copiar PIX** abaixo\n2️⃣ Cole no seu banco/app de pagamento\n3️⃣ Pague via PIX\n4️⃣ Clique em **✅ JÁ PAGUEI** após pagar", 
                        inline=False)

        guild = interaction.guild or await get_guild()
        view = PixView(pix_limpo, pay_id, produto, interaction.user, guild)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        
        asyncio.create_task(verificar_pagamento(pay_id, pedido_id, interaction.user, produto, guild))

    except Exception as e:
        print(f"[PAGAMENTO EXCEÇÃO] {type(e).__name__}: {e}")
        await interaction.followup.send(f"❌ Erro inesperado: `{str(e)[:200]}`", ephemeral=True)

async def verificar_pagamento(payment_id, pedido_id, user, produto, guild):
    for _ in range(30):
        await asyncio.sleep(10)
        try:
            info = sdk.payment().get(payment_id)
            if info["response"].get("status") == "approved":
                await update_pedido(pedido_id, "aprovado")
                await add_venda(produto["preco"])
                await entregar_produto(user, dict(produto), pedido_id, guild)
                await atualizar_vendas()
                return
        except:
            pass
    await update_pedido(pedido_id, "expirado")

# ================= ATUALIZAÇÃO DA VITRINE =================
async def montar_embed_loja():
    produtos = await get_produtos()
    embed = criar_embed(titulo="**🌸 B A N I D A  S T O R E**",
                        descricao="💎 **Compre via PIX e receba em canal exclusivo!**\n🔐 Arquivo criptografado + senha única\n⏰ Canal expira em **5 minutos**",
                        cor=0xFF69B4)
    for pid, p in produtos.items():
        desc = p.get("descricao") or ""
        arquivo = "📂 Arquivo incluído" if p.get("arquivo_nome") else "🔑 Acesso imediato"
        embed.add_field(name=f"{p['emoji']} {p['nome']}",
                        value=f"**{formatar_preco(p['preco'])}**\n🆔 `{pid}`\n{arquivo}" + (f"\n> {desc}" if desc else ""),
                        inline=True)
    embed.set_footer(text="🌸 BANIDA STORE • Clique em 💰 COMPRAR")
    embed.timestamp = datetime.utcnow()
    return embed

async def atualizar_loja():
    canal = bot.get_channel(CANAL_LOJA)
    if not canal:
        print("⚠️ Canal da loja não encontrado.")
        return
    async for msg in canal.history(limit=10):
        if msg.author == bot.user:
            try: await msg.delete()
            except: pass
    await canal.send(embed=await montar_embed_loja(), view=LojaButtons())

async def atualizar_vendas():
    canal = bot.get_channel(CANAL_VENDAS)
    if not canal:
        print("⚠️ Canal de vendas não encontrado.")
        return
    async for msg in canal.history(limit=10):
        if msg.author == bot.user:
            try: await msg.delete()
            except: pass
    total, qtd = await get_vendas()
    embed = criar_embed(titulo="📊 ESTATÍSTICAS", cor=COR_DESTAQUE)
    embed.add_field(name="📦 Vendas", value=f"**{qtd}**", inline=True)
    embed.add_field(name="💰 Faturamento", value=f"**{formatar_preco(total)}**", inline=True)
    embed.add_field(name="📈 Ticket Médio", value=formatar_preco(total/qtd) if qtd else "R$ 0,00", inline=True)
    await canal.send(embed=embed)

# ================= WEBHOOK =================
async def webhook_mp(request):
    try:
        data = await request.json()
        pay_id = data.get("data", {}).get("id") if data.get("type") == "payment" else None
        if pay_id and pay_id in pedidos_pendentes:
            info = sdk.payment().get(pay_id)
            if info["response"].get("status") == "approved":
                pedido_id = pedidos_pendentes[pay_id]
                async with db.acquire() as conn:
                    pedido = await conn.fetchrow("SELECT * FROM pedidos WHERE id=$1", pedido_id)
                    if pedido and pedido["status"] == "pendente":
                        user = await bot.fetch_user(pedido["user_id"])
                        produtos = await get_produtos()
                        produto = produtos.get(pedido["produto_id"])
                        if produto:
                            guild = await get_guild()
                            if guild:
                                await update_pedido(pedido_id, "aprovado")
                                await add_venda(produto["preco"])
                                await entregar_produto(user, dict(produto), pedido_id, guild)
                                await atualizar_vendas()
    except Exception as e:
        print(f"Webhook MP: {e}")
    return web.Response(status=200)

async def start_server():
    app = web.Application()
    app.router.add_post("/webhook", webhook_mp)
    app.router.add_get("/health", lambda r: web.Response(text="OK — BANIDA STORE"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"✅ Servidor HTTP ativo na porta {port}")

# ================= COMANDOS =================
@bot.command(name="loja")
async def cmd_loja(ctx):
    await ctx.send(embed=await montar_embed_loja(), view=LojaButtons())
    try: await ctx.message.delete()
    except: pass

@bot.command(name="vendas")
async def cmd_vendas(ctx):
    if not any(r.id == CARGO_DONO for r in ctx.author.roles):
        return
    total, qtd = await get_vendas()
    embed = criar_embed(titulo="📊 ESTATÍSTICAS", cor=COR_DESTAQUE)
    embed.add_field(name="📦 Vendas", value=f"**{qtd}**", inline=True)
    embed.add_field(name="💰 Faturamento", value=f"**{formatar_preco(total)}**", inline=True)
    embed.add_field(name="📈 Ticket Médio", value=formatar_preco(total/qtd) if qtd else "R$ 0,00", inline=True)
    await ctx.send(embed=embed)
    try: await ctx.message.delete()
    except: pass

@bot.command(name="upload")
async def cmd_upload(ctx, produto_id: str = None):
    if not any(r.id == CARGO_DONO for r in ctx.author.roles):
        return await ctx.reply("❌ Sem permissão.")
    if not produto_id:
        return await ctx.reply("❌ Uso: `!upload <produto_id>` com arquivo anexado.")
    if not ctx.message.attachments:
        return await ctx.reply("❌ Nenhum arquivo anexado.")
    produtos = await get_produtos()
    if produto_id not in produtos:
        return await ctx.reply(f"❌ Produto `{produto_id}` não encontrado.")
    att = ctx.message.attachments[0]
    if att.size/1024/1024 > 25:
        return await ctx.reply(f"❌ Arquivo muito grande.")
    msg = await ctx.reply(f"⏳ Salvando **{att.filename}**...")
    try:
        dados = await att.read()
        await salvar_arquivo_produto(produto_id, att.filename, dados)
        await msg.edit(content=f"✅ Arquivo **{att.filename}** salvo! Produto: `{produto_id}`")
        await atualizar_loja()
        await log_admin("Upload de Arquivo", ctx.author, f"**{att.filename}** • Produto `{produto_id}`")
    except Exception as e:
        await msg.edit(content=f"❌ Erro: {e}")

@bot.command(name="remover_arquivo")
async def cmd_remover_arquivo(ctx, produto_id: str = None):
    if not any(r.id == CARGO_DONO for r in ctx.author.roles):
        return
    if not produto_id:
        return await ctx.reply("❌ Use: `!remover_arquivo <produto_id>`")
    await remover_arquivo_produto(produto_id)
    await ctx.reply(f"✅ Arquivo removido do produto `{produto_id}`.")
    await atualizar_loja()
    await log_admin("Arquivo Removido", ctx.author, f"Produto `{produto_id}`")

@bot.command(name="check7z")
async def cmd_check7z(ctx):
    if not any(r.id == CARGO_DONO for r in ctx.author.roles):
        return
    if verificar_7zip():
        result = subprocess.run(["7z", "i"], capture_output=True, text=True, timeout=5)
        await ctx.reply(f"✅ **7-Zip instalado!**\n`{result.stdout.strip()}`")
    else:
        await ctx.reply("❌ **7-Zip NÃO encontrado.** Use `!instalar7z`.")

@bot.command(name="instalar7z")
async def cmd_instalar7z(ctx):
    if not any(r.id == CARGO_DONO for r in ctx.author.roles):
        return await ctx.reply("❌ Sem permissão.")
    msg = await ctx.reply("⏳ Instalando 7-Zip...")
    if instalar_7zip() and verificar_7zip():
        await msg.edit(content="✅ **7-Zip instalado com sucesso!**")
        await log_admin("7-Zip Instalado", ctx.author, "Instalação concluída")
    else:
        await msg.edit(content="❌ Falha na instalação.")

@bot.command(name="criar_painel_ticket")
@commands.has_permissions(administrator=True)
async def criar_painel_ticket(ctx):
    canal = bot.get_channel(CANAL_TICKET_PANEL)
    if not canal:
        return await ctx.send(f"❌ Canal {CANAL_TICKET_PANEL} não encontrado.")
    embed = discord.Embed(title="🎫 Central de Suporte - BANIDA STORE",
                          description="Precisa de ajuda? Abra um ticket.\n\n➡️ **Clique no botão abaixo.**",
                          color=COR_PRINCIPAL)
    embed.set_footer(text="🌸 BANIDA STORE • Atendimento rápido")
    embed.timestamp = datetime.utcnow()
    view = AbrirTicketView()
    await canal.send(embed=embed, view=view)
    await ctx.send(f"✅ Painel de tickets enviado em {canal.mention}!", delete_after=5)

@bot.command(name="painel_admin")
@commands.has_permissions(administrator=True)
async def cmd_painel_admin(ctx):
    canal = bot.get_channel(CANAL_PAINEL_ADMIN)
    if not canal:
        return await ctx.send(f"❌ Canal {CANAL_PAINEL_ADMIN} não encontrado.")
    embed = criar_embed(titulo="🌸 BANIDA STORE - Painel Administrativo",
                        descricao="Utilize os botões abaixo para gerenciar os produtos.",
                        cor=COR_DESTAQUE)
    view = AdminView()
    await canal.send(embed=embed, view=view)
    await ctx.send(f"✅ Painel admin enviado em {canal.mention}!", delete_after=5)

# ================= EVENTOS =================
@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user}")
    if not await init_db():
        print("❌ Falha no banco.")
        return
    if not verificar_7zip():
        print("⚠️ 7-Zip não encontrado. Instalando...")
        if instalar_7zip():
            print("✅ 7-Zip instalado.")
        else:
            print("❌ Falha no 7-Zip.")
    guild = await get_guild()
    if not guild:
        print(f"❌ Servidor {GUILD_ID} não encontrado.")
        return
    print(f"✅ Servidor: {guild.name}")
    await start_server()
    await atualizar_loja()
    await atualizar_vendas()

    # Painel de tickets
    canal_ticket = bot.get_channel(CANAL_TICKET_PANEL)
    if canal_ticket:
        async for msg in canal_ticket.history(limit=20):
            if msg.author == bot.user:
                try: await msg.delete()
                except: pass
        embed = discord.Embed(title="🎫 Central de Suporte - BANIDA STORE",
                              description="Precisa de ajuda? Abra um ticket.\n\n➡️ **Clique no botão abaixo.**",
                              color=COR_PRINCIPAL)
        embed.set_footer(text="🌸 BANIDA STORE • Atendimento rápido")
        embed.timestamp = datetime.utcnow()
        view = AbrirTicketView()
        await canal_ticket.send(embed=embed, view=view)
        print("✅ Painel de tickets enviado.")
    else:
        print(f"⚠️ Canal de ticket {CANAL_TICKET_PANEL} não encontrado.")

    # Painel admin
    canal_admin = bot.get_channel(CANAL_PAINEL_ADMIN)
    if canal_admin:
        async for msg in canal_admin.history(limit=20):
            if msg.author == bot.user:
                try: await msg.delete()
                except: pass
        embed = criar_embed(titulo="🌸 BANIDA STORE - Painel Administrativo",
                            descricao="Utilize os botões abaixo para gerenciar os produtos.",
                            cor=COR_DESTAQUE)
        view = AdminView()
        await canal_admin.send(embed=embed, view=view)
        print("✅ Painel admin enviado.")
    else:
        print(f"⚠️ Canal admin {CANAL_PAINEL_ADMIN} não encontrado.")

    print("✅ Bot pronto!")

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return
    custom_id = interaction.data.get("custom_id", "")
    # Os botões do PIX agora são tratados pela própria view PixView
    # Apenas mantemos os checks antigos para compatibilidade (se necessário)
    if custom_id.startswith("check_") or custom_id.startswith("cancel_"):
        # As views já tratam esses botões, então ignoramos aqui para evitar duplicidade
        pass

# ================= INÍCIO =================
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
