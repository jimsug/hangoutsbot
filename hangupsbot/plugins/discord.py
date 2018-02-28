import plugins
import discord
import asyncio
import logging
import aiohttp
import io, re, requests
import copy
from event import ConversationEvent

logger = logging.getLogger(__name__)

client = discord.Client()
_bot = None
sending = {}

already_seen_discord_messages = []

@client.event
@asyncio.coroutine
def on_ready():
    logger.debug('Logged into discord as {} {}'.format(client.user.name, client.user.id))

@client.event
@asyncio.coroutine
def on_message(message):
    if message.author == client.user:
        return
    try:
        if message.webhook_id:
            sendmsg = False
            return
    except AttributeError:
        pass
    
    if message.author.bot:
        return
    
    global already_seen_discord_messages
    if message.id in already_seen_discord_messages:
        return
    print("{0.channel.id}: {0.content}".format(message))
    already_seen_discord_messages.append(message.id)
    global sending
    if 'whereami' in message.content:
        yield from message.channel.send(message.channel.id)
    conv_config = _bot.config.get_by_path(["conversations"])
    for conv_id, config in conv_config.items():
        try:
            config_sync_channel = config['discord_sync']
            
        except KeyError:
            continue
        except TypeError:
            continue
        
        if str(message.channel.id) == config_sync_channel:
            fixed_message = re.sub(r"(@.*?)#0000", r"\1", message.clean_content)
            fixed_message = re.sub(r"<(:.*?:).*?>", r"\1", fixed_message)
            msg = "<b>{}</b>: {}".format(message.author.display_name, fixed_message)
            if conv_id not in sending:
              sending[conv_id] = 0
            sending[conv_id] += 1
            yield from _bot.coro_send_message(conv_id, msg, context={'discord': True})
            mentioning_user = _bot._user_list._self_user
            fake_event = ConversationEvent
            fake_event.conv_event = 0
            fake_event.timestamp = 0
            fake_event.conv_id = conv_id
            fake_event.conv = _bot._conv_list.get(conv_id)
            fake_event.user_id = mentioning_user.id_
            mentioning_user.full_name = "@" + message.author.display_name
            fake_event.user =  mentioning_user
            fake_event.text = fixed_message
            occurrences = [word for word in set(fake_event.text.split()) if word.startswith('@')]
            if len(occurrences) > 0:
                for word in occurrences:
                    cleaned_name = ''.join(e for e in word if e.isalnum() or e in ["-"])
                    yield from plugins.mentions.mention( _bot, fake_event, cleaned_name)
            # yield from plugins.subscribe._handle_keyword( _bot, fake_event, "a")

            for a in message.attachments:
              r = yield from aiohttp.request('get', str(a.url))
              raw = yield from r.read()
              image_data = io.BytesIO(raw)
              logger.info("uploading: {}".format(str(a.url)))
              sending[conv_id] += 1
              image_id = yield from _bot._client.upload_image(image_data, filename=str(a.filename))
              yield from _bot.coro_send_message(conv_id, None, image_id=image_id, context={'discord': True})

def _initialise(bot):
    global _bot, client
    _bot = bot
    token = bot.get_config_option('discord_token')
    if not token:
        logger.error("discord_token not set")
        return

    plugins.register_handler(_handle_hangout_message, type="allmessages")
    plugins.register_user_command(["dusers"])
    plugins.register_admin_command(['dsync', 'discordfwd'])

    try:
        client.run(token)
    except RuntimeError:
        # client.run will try start an event loop, however this will fail as hangoutsbot will have already started one
        # this isn't anything to worry about
        pass

@asyncio.coroutine
def dusers(bot, event, *text):
    """List users in the synced Discord channel"""
    print(", ".join([server for server in client.guilds]))
    try:
        conv_config = bot.config.get_by_path(["conversations", event.conv_id])
        discord_channelid = conv_config["discord_sync"]
        print("Synced to {}".format(discord_channelid))
    except:
        msg = "This chat isn't synced to any Discord channel"
    
    if discord_channelid:
        channel = client.get_channel(str(discord_channelid))
        print("Channel name: {}".format(channel.name))
        members = []
        for member in channel.server.members:
            if channel.permissions_for(member).read_messages:
                members.append(member.nickname or member.name)
        
        print(members)

def get_discord_channel(bot, hangoutid):
    try:
        channelconfig = bot.config.get_by_path(['conversations', hangoutid])
        discord_channel_id = channelconfig['discord_sync']
        if discord_channel_id:
            return client.get_channel(int(discord_channel_id))
    except Exception as e:
        logger.info(str(e))
        return False
        

def _handle_hangout_message(bot, event, command):
    try:
        channelconfig = bot.config.get_by_path(['conversations', event.conv_id])
    except Exception as e:
        logger.error(str(e))
        return
    
    discord_channel = get_discord_channel(bot, event.conv_id)
    if not discord_channel:
        syncouts = bot.get_config_option("sync_rooms") or []
        syncout = False
        for sync_room_list in syncouts:
            if event.conv_id in sync_room_list:
                syncout = sync_room_list
                break
                
        if not syncout:
            pass
        else:
            for sync_room in syncout:
                discord_channel = get_discord_channel(bot, sync_room)
                if discord_channel:
                    break
                
    try:
        if event.user.is_self and event._slackrtm_no_repeat:
            return
    except AttributeError:
        if event.user.is_self:
            return
        

    discord_webhook = bot.get_config_suboption(event.conv_id, 'discord_webhook')
    if discord_webhook:
        if (bot.memory.exists(['user_data', event.user_id.chat_id, 'nickname']) and len(bot.get_memory_suboption(event.user_id.chat_id, 'nickname').strip()) > 0):
            username = bot.get_memory_suboption(event.user_id.chat_id, 'nickname').strip()
        else:
            username = event.user.full_name
            
        try:
            if len(bot._user_list.get_user(event.user_id).photo_url) > 0:
                avatar = "http:" + bot._user_list.get_user(event.user_id).photo_url
            else:
                avatar = ""
        except Exception as e:
            avatar = ""

    if channelconfig['discord_forward']:
        try:
            assert not isinstance(discord_webhook, str)
            discord_url = discord_webhook.pop()
            discord_webhook.insert(0, discord_url)
            bot.config.set_by_path(["conversations", event.conv_id, 'discord_webhook'], discord_webhook)
        except AssertionError:
            discord_url = discord_webhook
    elif channelconfig['discord_channel']:
        channel = channelconfig['discord_channel']
        discord_url = "https://discordapp.com/api/channels/{}/messages".format(channel)

    html_message = ""
    try:
        for segment in event.conv_event.segments:
            if (not segment) or (segment.type_ == 1):
                html_message += "\n"
                continue
            else: 
                html_message += markdownify(segment)

    except AttributeError:
        for segment in event.conv_event.ChatMessageSegment:
            if segment.type_ == hangups.schemas.SegmentType.TEXT:
                html_message = event.text
    except TypeError:
        try:
            for segment in event.conv_event.segments:
                if not segment:
                    html_message += "<br />"
                    continue
                elif segment.type_ == hangups.schemas.SegmentType.TEXT:
                    html_message = event.text
                else: 
                    html_message += markdownify(segment)
        except TypeError:
            for segment in event.conv_event.text:
                if not segment:
                    html_message += "<br />"
                    continue
                else: 
                    html_message = event.text
    
    for a in event.conv_event.attachments:
        html_message += "\n" + a
    
    if discord_channel:
        members = False
        for word in html_message.split():
            if word == "@everyone":
                html_message = html_message.replace(word, "@-everyone")
            elif word == "@here":
                html_message = html_message.replace(word, "@-here")
            elif word.startswith("@"):
                if not members:
                    members = discord_channel.guild.members
                member = False
                mentioned_name = str(word[1:]).strip().lower()
                for user in members:
                    try:
                        if mentioned_name in [str(user.nickname).strip().lower(), str(user.name).strip().lower()]:
                            member = user
                            break
                    except AttributeError:
                        if mentioned_name == user.name.strip().lower():
                            member = user
                            break
                if member:
                    html_message = html_message.replace(word, "<@!{}>".format(member.id))
    
    if channelconfig['discord_forward']:
        body = {u"content":u"{}".format(html_message), u"username":u"{}".format(username), u"avatar_url":u"{}".format(avatar)}
        r = requests.post(discord_url, data=body)
    elif channelconfig['discord_channel']:
        body = {u"content":u"{}".format(html_message)}
        headers = {"Authorization":"Bot {}".format(bot.memory.get_by_path(['discord_token']))}
        r = requests.post(discord_url, data=body, headers=headers)
        

def markdownify(segment):
    text = segment.text
    prefix = ""
    if segment.is_bold:
        prefix += "**"
    if segment.is_italic:
        prefix += "_"
    if segment.is_underline:
        prefix =+ "__"

    if prefix:
        suffix = prefix[::-1]
        text = prefix + text + suffix

    return text 


def dsync(bot, event, discord_channel=None, convid=False, mode="forwarding"):
    conv_id = convid or event.conv_id
    if discord_channel == None:
        bot.config.set_by_path(["conversations", conv_id, "discord_forward"], False)
        bot.config.set_by_path(["conversations", conv_id, "discord_webhook"], [])
        bot.config.set_by_path(["conversations", conv_id, "discord_sync"], None)
        msg = "Hangout disconnected from Discord"
        yield from bot.coro_send_message(event.conv_id, msg)
        return
    
    ''' Sync a hangout to a discord channel. Usage - "/bot dsync 123456789" Say "whereami" in the channel once the bot has been added to get the channel id" '''
    channel = client.get_channel(int(discord_channel))
    webhooks = yield from channel.webhooks()
    existing_webhooks = []
    for webhook in webhooks:
        print(webhook.name)
        if webhook.name.startswith(str(conv_id)[:30]):
            existing_webhooks.append(webhook)
            if len(existing_webhooks) >= 2:
                break
    
    while len(existing_webhooks) < 2:
        new_webhook_name = "{}-{}".format(str(conv_id)[:30], str(len(existing_webhooks)))
        try:
            new_webhook = yield from channel.create_webhook(name=new_webhook_name)
            existing_webhooks.append(new_webhook)
        except HTTPException:
            logger.error("Something went wrong while creating webhooks for forwarding")
        
    try:
      bot.config.set_by_path(["conversations", conv_id, "discord_sync"], discord_channel)
    except KeyError:
      bot.config.set_by_path(["conversations", conv_id], {"discord_sync": discord_channel})
    
    # conv_id = convid or event.conv_id
    try:
        bot.config.get_by_path(["conversations", conv_id])
    except:
        bot.config.set_by_path(["conversations", conv_id], {})
    
    if len(existing_webhooks) >= 2:
        bot.config.set_by_path(["conversations", conv_id, "discord_forward"], True)
        bot.config.set_by_path(["conversations", conv_id, "discord_webhook"], [webhook.url for webhook in existing_webhooks])

    # if mode == "forwarding":
    #     bot.config.set_by_path(["conversations", event.conv_id, "discord_forward"],)
    
    bot.config.save()
    msg = "Synced Hangout **{}** to Discord channel **#{}** in **{}**".format(bot.conversations.get_name(conv_id), channel.name, channel.guild.name)
    yield from bot.coro_send_message(event.conv_id, msg)
    
def discordfwd(bot, event, url1="", url2="", convid=""):
    '''/bot discordfwd <url1> <url2> [convid]

    Enable forwarding of messages to the specified urls. The bot will alternate between the two webhooks to ensure correct authorship in Discord. If less than two urls are specified, the bot will disable forwarding and remove any stored webhooks. 
    
    If alternate Discord forwarding is enabled, it will be disabled.
    
    A Hangout conversation id may be optionally provided as a third parameter, in which case these changes will be applied to that Hangout. Otherwise, the changes will be applied to the present Hangout.

    If disabling forwarding the command must be run in the required Hangout'''

    discordurl = re.compile("https://(canary\.)?discordapp\.com\/api\/webhooks\/\d{18}\/.+")

    if not discordurl.match(url1) or not discordurl.match(url2):
        msg = "Webhooks deleted. Forwarding disabled."
        try:
            bot.config.get_by_path(["conversations", event.conv_id])
            bot.config.get_by_path(["conversations", event.conv_id, "discord_forward"])
        except:
            bot.config.set_by_path(["conversations", event.conv_id], {})

        bot.config.set_by_path(["conversations", event.conv_id, "discord_webhook"], [])
        bot.config.set_by_path(["conversations", event.conv_id, "discord_forward"], False)
    elif url1 == url2:
        msg = "Those two webhooks are the same."
    elif discordurl.match(url1) and discordurl.match(url2):
        conv_id = convid or event.conv_id
        try:
            bot.config.get_by_path(["conversations", conv_id])
        except:
            bot.config.set_by_path(["conversations", conv_id], {})

        bot.config.set_by_path(["conversations", conv_id, "discord_forward"], True)
        bot.config.set_by_path(["conversations", conv_id, "discord_webhook"], [url1, url2])
        msg = "Forwarding to discord enabled for {}.".format(conv_id)


    yield from bot.coro_send_message(event.conv, "{}: {}".format(event.user.full_name, msg))
