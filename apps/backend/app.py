"""
Echo Speak - Main Application Entry Point.
A standalone agentic voice AI system using LangChain.
Supports multiple LLM providers: OpenAI, Ollama, LM Studio, LocalAI, llama.cpp, vLLM.
"""

import os
import sys
import argparse
import threading
from loguru import logger

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import config, ModelProvider
from agent.core import create_agent, list_available_providers, get_provider_requirements
from io_module.voice import create_voice_manager
from api.server import start_server


def setup_logging():
    """Configure logging for the application."""
    logger.remove()
    log_file = config.logs_path / "echospeak.log"
    logger.add(
        log_file,
        rotation="10 MB",
        retention="10 days",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
    )
    logger.add(
        sys.stderr,
        level="INFO",
        format="{time:HH:mm:ss} | {level} | {message}"
    )


def process_voice_query(voice_manager, agent, query: str) -> str:
    """
    Process a voice query through the agent.

    Args:
        voice_manager: Voice manager instance.
        agent: Agent instance.
        query: User query.

    Returns:
        Agent response.
    """
    logger.info(f"Processing query: {query}")

    response, success = agent.process_query(query)

    if success:
        return response
    else:
        return "I encountered an error processing your request. Please try again."


def select_provider_interactive() -> ModelProvider:
    """
    Interactive provider selection menu.

    Returns:
        Selected ModelProvider.
    """
    providers = list_available_providers()

    print("\n" + "=" * 60)
    print("Echo Speak - Select Model Provider")
    print("=" * 60)

    for i, p in enumerate(providers, 1):
        status = "[LOCAL]" if p["local"] else "[CLOUD]"
        print(f"{i}. {p['name']} {status}")
        print(f"   {p['description']}")

    print("=" * 60)

    while True:
        try:
            choice = input("Select provider (1-{}): ".format(len(providers))).strip()
            idx = int(choice) - 1
            if 0 <= idx < len(providers):
                provider_id = providers[idx]["id"]
                return ModelProvider(provider_id)
        except (ValueError, IndexError):
            pass

        print("Invalid selection. Please try again.")


def show_provider_info(provider: ModelProvider):
    """Show information about a provider."""
    info = get_provider_requirements(provider)
    print(f"\nProvider: {provider.value}")
    print(f"Description: {info.get('description', 'N/A')}")
    print(f"Environment variables needed: {', '.join(info.get('env_vars', []))}")


def run_voice_mode(agent):
    """
    Run the voice interaction mode.

    Args:
        agent: Agent instance.
    """
    # Check if PersonaPlex is enabled
    if config.personaplex.enabled:
        logger.info("Starting PersonaPlex voice mode...")
        try:
            from io_module.personaplex_client import run_personaplex_voice_mode
            run_personaplex_voice_mode(agent)
            return
        except ImportError as e:
            logger.warning(f"PersonaPlex dependencies not available: {e}")
            logger.info("Falling back to standard voice mode")
        except Exception as e:
            logger.error(f"PersonaPlex failed: {e}")
            logger.info("Falling back to standard voice mode")

    logger.info("Starting voice mode...")

    voice_manager = create_voice_manager()

    provider_name = agent.llm_provider.value.upper()
    voice_manager.output.speak(f"Echo Speak is ready using {provider_name}. Say 'hey echo' to start a conversation.")

    wake_words = ["hey echo", "jarvis"]

    wake_word_active = False

    while True:
        try:
            text = voice_manager.input.listen(timeout=5.0)

            if text:
                text_lower = text.lower()

                if not wake_word_active:
                    should_respond = any(wake_word in text_lower for wake_word in wake_words)

                    if should_respond:
                        logger.info(f"Wake word detected: {text}")
                        voice_manager.output.speak("Yes, I'm listening. What can I help you with?")
                        wake_word_active = True
                else:
                    wake_word_active = False
                    logger.info(f"User query: {text}")
                    response = process_voice_query(voice_manager, agent, text)
                    logger.info(f"Response: {response[:60]}...")
                    voice_manager.output.speak(response)

            else:
                logger.debug("No speech detected within timeout")

        except KeyboardInterrupt:
            logger.info("Voice mode interrupted by user")
            voice_manager.output.speak("Goodbye!")
            break
        except Exception as e:
            logger.error(f"Error in voice mode: {e}")
            try:
                voice_manager.output.speak("An error occurred. Please try again.")
            except:
                pass


def run_text_mode(agent):
    """
    Run the text-based interaction mode with voice output.

    Args:
        agent: Agent instance.
    """
    logger.info("Starting text mode with voice...")

    from io_module.voice import create_voice_manager
    voice_manager = create_voice_manager()

    print("\n" + "=" * 60)
    print("Echo Speak - Voice AI Assistant")
    print("=" * 60)
    print(f"Provider: {agent.llm_provider.value.upper()}")
    print("Commands:")
    print("  'quit' or 'exit' - Exit the application")
    print("  'clear' - Clear conversation history")
    print("  'history' - View conversation history")
    print("  'provider' - Switch model provider")
    print("  'info' - Show provider information")
    print("  'voice on' - Enable voice output")
    print("  'voice off' - Disable voice output")
    print("=" * 60 + "\n")

    voice_enabled = True

    while True:
        try:
            user_input = input("You: ").strip()

            if user_input.lower() in ["quit", "exit"]:
                print("Goodbye!")
                voice_manager.output.speak("Goodbye!")
                break

            if user_input.lower() == "clear":
                agent.clear_conversation()
                print("Conversation history cleared.\n")
                continue

            if user_input.lower() == "history":
                history = agent.get_history()
                for msg in history:
                    print(f"  {msg}\n")
                continue

            if user_input.lower() == "provider":
                new_provider = select_provider_interactive()
                show_provider_info(new_provider)
                agent.switch_provider(new_provider)
                print(f"Switched to {new_provider.value.upper()}\n")
                continue

            if user_input.lower() == "info":
                info = agent.provider_info
                print(f"\nCurrent Provider: {info['provider']}")
                print(f"Model: {info['model']}")
                print(f"Memory Items: {info['memory_count']}\n")
                continue

            if user_input.lower() == "voice off":
                voice_enabled = False
                print("Voice output disabled.\n")
                continue

            if user_input.lower() == "voice on":
                voice_enabled = True
                print("Voice output enabled.\n")
                continue

            if not user_input:
                continue

            response, success = agent.process_query(user_input)

            if success:
                print(f"Echo: {response}\n")
                if voice_enabled:
                    try:
                        voice_manager.output.speak(response)
                    except Exception as e:
                        logger.warning(f"Voice output failed: {e}")
            else:
                print(f"Error: {response}\n")
                if voice_enabled:
                    try:
                        voice_manager.output.speak(response)
                    except:
                        pass

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            logger.error(f"Error in text mode: {e}")
            print(f"An error occurred: {e}\n")


def run_api_mode(host: str = None, port: int = None):
    """Run the API server mode."""
    logger.info("Starting API mode...")
    start_server(host=host, port=port)


def main():
    """Main entry point for Echo Speak."""
    parser = argparse.ArgumentParser(description="Echo Speak - Voice AI Assistant")
    parser.add_argument(
        "--mode",
        choices=["voice", "text", "api"],
        default="text",
        help="Interaction mode: voice, text, or api (default: text)"
    )
    parser.add_argument(
        "--provider",
        choices=[p.value for p in ModelProvider],
        default=None,
        help="Model provider to use"
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="API server host"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="API server port"
    )
    parser.add_argument(
        "--list-providers",
        action="store_true",
        help="List available model providers"
    )

    args = parser.parse_args()

    setup_logging()

    if args.list_providers:
        providers = list_available_providers()
        print("\nAvailable Model Providers:")
        print("=" * 60)
        for p in providers:
            status = "LOCAL" if p["local"] else "CLOUD"
            print(f"{p['id']:12} | {p['name']:12} | [{status}]")
            print(f"             {p['description']}")
        print("=" * 60)
        return

    logger.info(f"Starting Echo Speak")

    if args.provider:
        provider = ModelProvider(args.provider)
        show_provider_info(provider)
        agent = create_agent(provider=provider)
    else:
        if config.use_local_models:
            agent = create_agent(provider=config.local.provider)
        else:
            agent = create_agent()

    if args.mode == "api":
        run_api_mode(args.host, args.port)
    elif args.mode == "voice":
        run_voice_mode(agent)
    else:
        run_text_mode(agent)


if __name__ == "__main__":
    main()
