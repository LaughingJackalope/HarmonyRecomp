// harmonyrecomp - ReXGlue Recompiled Project
//
// Customize your app by overriding virtual hooks from rex::ReXApp.

#pragma once

#include <cstdlib>
#include <filesystem>
#include <memory>
#include <string>
#include <system_error>
#include <utility>
#include <vector>

#include <toml++/toml.hpp>

#include <rex/cvar.h>
#include <rex/filesystem.h>
#include <rex/graphics/command_processor.h>
#include <rex/graphics/graphics_system.h>
#include <rex/logging.h>
#include <rex/rex_app.h>
#include <rex/system/flags.h>
#include <rex/ui/keybinds.h>
#include <rex/ui/ui_event.h>
#include <rex/ui/window.h>
#include <rex/ui/window_listener.h>


#include "controller_remap.h"
#include "crash_log.h"
#include "dlc.h"
#include "game_fixes.h"
#include "graphics_menu.h"
#include "saves.h"
#include "netplay/netplay.h"
#include "notice.h"
#include "update_check.h"

class HarmonyrecompApp : public rex::ReXApp {
 public:
  using rex::ReXApp::ReXApp;

  static std::unique_ptr<rex::ui::WindowedApp> Create(
      rex::ui::WindowedAppContext& ctx) {
    return std::unique_ptr<HarmonyrecompApp>(new HarmonyrecompApp(ctx, "harmonyrecomp",
        PPCImageConfig));
  }

  // Remember where the runtime settings live (harmonyrecomp.toml next to the
  // exe), so the graphics menu saves to the same file the F4 menu uses.
  //
  // Also find the game data without --game_data_root. The SDK reads that
  // option before it loads harmonyrecomp.toml, so a value in the file alone
  // would never be used; read it here ourselves, then fall back to the
  // project's assets folder relative to the exe.
  void OnConfigurePaths(rex::PathConfig& paths) override {
    harmony::RaiseTimerResolution();
#if defined(__APPLE__)
    UseAppBundlePaths(paths);
#endif
    config_path_ = paths.config_path;
    FindGameDataRoot(paths);
    // With title update 2 the game reads its updated files from the update:
    // device (data/player, data/text, ...). build.py copied them over the
    // game data, next to default.xexp, so update: is the game data folder too.
    std::error_code ec;
    title_update_ = !paths.game_data_root.empty() &&
                    std::filesystem::exists(paths.game_data_root / "default.xexp", ec);
    if (paths.update_data_root.empty() && title_update_) {
      paths.update_data_root = paths.game_data_root;
    }
  }

#if defined(__APPLE__)
  // Inside HarmonyRecomp.app (scripts/package_macos.py) nothing may be written
  // into the bundle: that breaks its code signature, and /Applications may not
  // be writable. Settings and logs go to ~/Library/Application Support/
  // HarmonyRecomp; the first start copies the default settings from
  // Contents/Resources. Saves stay in the SDK's user folder
  // (~/.local/share/harmonyrecomp), as for a build run from out/.
  void UseAppBundlePaths(rex::PathConfig& paths) {
    const auto exe_dir = rex::filesystem::GetExecutableFolder();
    const auto contents = exe_dir.parent_path();
    if (exe_dir.filename() != "MacOS" || contents.filename() != "Contents" ||
        contents.parent_path().extension() != ".app") {
      return;
    }
    const char* home = std::getenv("HOME");
    if (!home || !home[0]) {
      return;
    }
    const auto support =
        std::filesystem::path(home) / "Library" / "Application Support" / "HarmonyRecomp";
    std::error_code ec;
    std::filesystem::create_directories(support / "logs", ec);
    const auto config = support / "harmonyrecomp.toml";
    if (!std::filesystem::exists(config, ec)) {
      std::filesystem::copy_file(contents / "Resources" / "harmonyrecomp.toml", config, ec);
    }
    paths.config_path = config;
    rex::cvar::SetFlagByName("log_file", (support / "logs" / "harmonyrecomp.log").string());
  }
#endif

  void FindGameDataRoot(rex::PathConfig& paths) {
    if (!paths.game_data_root.empty()) {
      return;
    }
    std::vector<std::filesystem::path> candidates;
    if (auto from_file = ReadGameDataRootFromConfig(); !from_file.empty()) {
      candidates.push_back(from_file);
    }
    const auto exe_dir = rex::filesystem::GetExecutableFolder();
    candidates.push_back(exe_dir / "assets");
    candidates.push_back(exe_dir / ".." / "Resources" / "assets");  // HarmonyRecomp.app
    candidates.push_back(exe_dir / ".." / ".." / ".." / "assets");  // out/build/<preset>/
    for (const auto& dir : candidates) {
      std::error_code ec;
      if (std::filesystem::exists(dir / "default.xex", ec)) {
        paths.game_data_root = std::filesystem::weakly_canonical(dir, ec);
        if (ec) {
          paths.game_data_root = dir;
        }
        return;
      }
    }
  }

  // A title update applied at build time leaves default.xexp and *.dllp next
  // to the binaries; the SDK's loader applies them by itself (the recompiled
  // code was generated from the patched modules).

  // Since df2743b the GPU emulation is a plugin next to the executable
  // (librexgpu-xenos, staged by GPU_PLUGINS in CMakeLists.txt); without one
  // the game runs without a picture. gpu_plugin in the settings still wins.
  void OnPreSetup(rex::RuntimeConfig& config) override {
    if (config.gpu_plugin.empty()) {
      config.gpu_plugin = "xenos";
    }
  }

  // Runtime and kernel exist now; start the LAN lobby service if netplay is on.
  void OnPostSetup() override {
    harmony::InstallGameFixes(runtime());
    harmony::InstallControllerRemap(runtime());
    // Other players must run the same game version; DLC differences are only
    // pointed out (see netplay/lobby.cpp).
    harmony::netplay::SetBuildInfo(title_update_ ? "2.04" : "1.04", harmony::BuiltDlcPackages());
    harmony::netplay::Startup();
  }

  // The game is loaded (title ID known) but its main thread has not started:
  // install the player's DLC packages so the game's content scan finds them.
  void OnPostLaunchModule(rex::system::XThread* thread) override {
    (void)thread;
    harmony::InstallDlcPackages(runtime());
    harmony::saves::SetUserDataRoot(runtime()->user_data_root());
  }

  // Logging is up now; report a settings file the SDK could not parse. The SDK
  // logs that error before the log file exists, so it was invisible so far.
  void OnPostInitLogging() override {
    harmony::InstallCrashLog();
    std::error_code ec;
    if (config_path_.empty() || !std::filesystem::exists(config_path_, ec)) {
      return;
    }
    try {
      (void)toml::parse_file(config_path_.string());
    } catch (const toml::parse_error& err) {
      std::string message = std::string(err.description());
      const auto& where = err.source().begin;
      message += " (line " + std::to_string(where.line) + ", column " +
                 std::to_string(where.column) + ")";
      REXLOG_ERROR("{} is invalid and was ignored: {}", config_path_.filename().string(), message);
      harmony::SetConfigError(std::move(message));
    }
  }

  void OnCreateDialogs(rex::ui::ImGuiDrawer* drawer) override {
    drawer_ = drawer;
    harmony::notice::Init(drawer, &app_context());
    rex::ui::RegisterBind("bind_graphics_menu", "F6", "Toggle settings menu",
                          [this] { ToggleGraphicsMenu(); });
    rex::ui::RegisterBind("bind_fps_counter", "F2", "Toggle FPS counter",
                          [this] { SetFpsCounter(!harmony::ShowFps()); });
    // Fullscreen is borderless (a window covering the monitor, no mode change).
    rex::ui::RegisterBind("bind_fullscreen", "F11", "Toggle fullscreen", [this] {
      if (auto* w = window()) w->SetFullscreen(!w->IsFullscreen());
    });
#if defined(__APPLE__)
    // macOS takes F11 for itself (Show Desktop / media key): also accept the
    // system's own fullscreen shortcut, Ctrl+Cmd+F.
    if (auto* w = window()) {
      mac_keys_.app = this;
      w->AddInputListener(&mac_keys_, 0);
    }
#endif
    // 30/60 FPS for speedruns (glitches that need the console's frame drops).
    rex::ui::RegisterBind("bind_fps_30_60", "F7", "Switch frame rate limit 30/60", [] {
      const bool at_30 =
          rex::cvar::GetFlagByName("vsync") == "true" && rex::cvar::GetFlagByName("vsync_rate") == "30";
      rex::cvar::SetFlagByName("vsync", "true");
      rex::cvar::SetFlagByName("vsync_rate", at_30 ? "60" : "30");
      harmony::notice::Post(at_30 ? "Frame rate limit: 60 FPS" : "Frame rate limit: 30 FPS", 3.0);
    });
    if (harmony::ShowFps()) {
      SetFpsCounter(true);
    }
    REXLOG_INFO("HarmonyRecomp {}", harmony::BuildVersion());
    harmony::StartUpdateCheck();
  }

  // Runs before the SDK tears down the ImGui drawer.
  void OnShutdown() override {
    RememberWindow();
    harmony::netplay::Shutdown();
    rex::ui::UnregisterBind("bind_graphics_menu");
    rex::ui::UnregisterBind("bind_fps_counter");
    rex::ui::UnregisterBind("bind_fullscreen");
#if defined(__APPLE__)
    if (auto* w = window()) w->RemoveInputListener(&mac_keys_);
#endif
    rex::ui::UnregisterBind("bind_fps_30_60");
    graphics_menu_.reset();
    fps_counter_.reset();
    harmony::notice::Shutdown();
    drawer_ = nullptr;
  }

 private:
  // The next start opens the window as it was left: fullscreen or not, and
  // the window's size (window_width/window_height, logical pixels).
  void RememberWindow() {
    auto* w = window();
    if (!w || config_path_.empty()) return;
    std::vector<std::pair<std::string, std::string>> values;
    values.push_back({"fullscreen", w->IsFullscreen() ? "true" : "false"});
    if (!w->IsFullscreen()) {
      const uint32_t width = w->GetActualLogicalWidth();
      const uint32_t height = w->GetActualLogicalHeight();
      if (width >= 320 && height >= 180) {
        values.push_back({"window_width", std::to_string(width)});
        values.push_back({"window_height", std::to_string(height)});
      }
    }
    harmony::WriteConfigValues(config_path_, values);
  }

  std::filesystem::path ReadGameDataRootFromConfig() const {
    std::error_code ec;
    if (config_path_.empty() || !std::filesystem::exists(config_path_, ec)) {
      return {};
    }
    try {
      auto table = toml::parse_file(config_path_.string());
      if (auto value = table["game_data_root"].value<std::string>()) {
        return std::filesystem::path(*value);
      }
    } catch (const toml::parse_error&) {
      // Reported in OnPostInitLogging once logging is available.
    }
    return {};
  }

  // Dialogs are created when switched on and destroyed when switched off.
  // A dialog never destroys itself: the FPS checkbox lives in the graphics
  // menu and only adds/removes the FPS window, which the SDK's drawer handles
  // safely during its draw loop (RemoveDialog adjusts the loop index).
  void ToggleGraphicsMenu() {
    if (graphics_menu_) {
      graphics_menu_.reset();
    } else if (drawer_) {
      graphics_menu_ = std::make_unique<harmony::GraphicsMenuDialog>(
          drawer_, config_path_, [this](bool on) { SetFpsCounter(on); });
    }
  }

  void SetFpsCounter(bool on) {
    harmony::SetShowFps(on);
    if (!on) {
      fps_counter_.reset();
    } else if (!fps_counter_ && drawer_) {
      fps_counter_ = std::make_unique<harmony::FpsCounterDialog>(
          drawer_, [this] { return GuestFrameCount(); });
    }
  }

  // Guest frames = the game's buffer swaps (swap_count, SDK change 12; counter()
  // would add the 60 vblanks per second).
  uint32_t GuestFrameCount() const {
    auto* gs = static_cast<rex::graphics::GraphicsSystem*>(runtime() ? runtime()->graphics_system()
                                                                      : nullptr);
    auto* cp = gs ? gs->command_processor() : nullptr;
    return cp ? cp->swap_count() : 0;
  }

#if defined(__APPLE__)
  struct MacKeys final : rex::ui::WindowInputListener {
    HarmonyrecompApp* app = nullptr;
    void OnKeyDown(rex::ui::KeyEvent& e) override {
      if (e.virtual_key() == rex::ui::VirtualKey::kF && e.is_ctrl_pressed() && e.is_super_pressed() &&
          !e.prev_state()) {
        if (auto* w = app->window()) w->SetFullscreen(!w->IsFullscreen());
        e.set_handled(true);
      }
    }
  };
  MacKeys mac_keys_;
#endif

  std::filesystem::path config_path_;
  bool title_update_ = false;
  rex::ui::ImGuiDrawer* drawer_ = nullptr;
  std::unique_ptr<harmony::GraphicsMenuDialog> graphics_menu_;
  std::unique_ptr<harmony::FpsCounterDialog> fps_counter_;
};
