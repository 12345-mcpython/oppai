#include "AppDelegate.h"

#include <cstdlib>   // [oppai] JS-DEBUGGER: atoi

#include "SimpleAudioEngine.h"
#include "jsb_cocos2dx_auto.hpp"
#include "jsb_cocos2dx_ui_auto.hpp"
#include "jsb_cocos2dx_studio_auto.hpp"
#include "jsb_cocos2dx_builder_auto.hpp"
#include "jsb_cocos2dx_spine_auto.hpp"
#include "jsb_cocos2dx_extension_auto.hpp"
#include "jsb_cocos2dx_3d_auto.hpp"
#include "jsb_cocos2dx_3d_extension_auto.hpp"
#include "3d/jsb_cocos2dx_3d_manual.h"
#include "ui/jsb_cocos2dx_ui_manual.h"
#include "cocostudio/jsb_cocos2dx_studio_manual.h"
#include "cocosbuilder/js_bindings_ccbreader.h"
#include "spine/jsb_cocos2dx_spine_manual.h"
#include "extension/jsb_cocos2dx_extension_manual.h"
#include "localstorage/js_bindings_system_registration.h"
#include "chipmunk/js_bindings_chipmunk_registration.h"
#include "jsb_opengl_registration.h"
#include "network/XMLHTTPRequest.h"
#include "network/jsb_websocket.h"
#include "network/jsb_socketio.h"

#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
#include "platform/android/CCJavascriptJavaBridge.h"
#elif (CC_TARGET_PLATFORM == CC_PLATFORM_IOS || CC_TARGET_PLATFORM == CC_PLATFORM_MAC)
#include "platform/ios/JavaScriptObjCBridge.h"
#endif

#include "CodeIDESupport.h"
#include "Runtime.h"
#include "ConfigParser.h"

// ---- 游戏自研绑定（重写版）----
#include "jsb_oppai_utilsex.h"
#include "jsb_oppai_gameshare.h"
#include "jsb_oppai_xg.h"
#include "jsb_oppai_talkingdata.h"

USING_NS_CC;
using namespace CocosDenshion;

AppDelegate::AppDelegate()
{
}

AppDelegate::~AppDelegate()
{
    ScriptEngineManager::destroyInstance();
#if (COCOS2D_DEBUG > 0 && CC_CODE_IDE_DEBUG_SUPPORT > 0)
	// NOTE:Please don't remove this call if you want to debug with Cocos Code IDE
	endRuntime();
#endif

	ConfigParser::purge();
}

void AppDelegate::initGLContextAttrs()
{
    GLContextAttrs glContextAttrs = {8, 8, 8, 8, 24, 8};
    
    GLView::setGLContextAttrs(glContextAttrs);
}

bool AppDelegate::applicationDidFinishLaunching()
{
#if (COCOS2D_DEBUG > 0 && CC_CODE_IDE_DEBUG_SUPPORT > 0)
    // NOTE:Please don't remove this call if you want to debug with Cocos Code IDE
    initRuntime();
#endif
    // initialize director
    auto director = Director::getInstance();
    auto glview = director->getOpenGLView();    
    if(!glview) {
        Size viewSize = ConfigParser::getInstance()->getInitViewSize();
        string title = ConfigParser::getInstance()->getInitViewName();
#if (CC_TARGET_PLATFORM == CC_PLATFORM_WIN32 || CC_TARGET_PLATFORM == CC_PLATFORM_MAC) && (COCOS2D_DEBUG > 0 && CC_CODE_IDE_DEBUG_SUPPORT > 0)
        extern void createSimulator(const char* viewName, float width, float height,bool isLandscape = true, float frameZoomFactor = 1.0f);
        bool isLanscape = ConfigParser::getInstance()->isLanscape();
        createSimulator(title.c_str(), viewSize.width,viewSize.height, isLanscape);
#else
        glview = cocos2d::GLViewImpl::createWithRect(title.c_str(), Rect(0, 0, viewSize.width, viewSize.height));
        director->setOpenGLView(glview);
#endif
    }

    // set FPS. the default value is 1.0/60 if you don't call this
    director->setAnimationInterval(1.0 / 60);
    
    ScriptingCore* sc = ScriptingCore::getInstance();
    sc->addRegisterCallback(register_all_cocos2dx);
    sc->addRegisterCallback(register_cocos2dx_js_core);
    sc->addRegisterCallback(jsb_register_system);

    // extension can be commented out to reduce the package
    sc->addRegisterCallback(register_all_cocos2dx_extension);
    sc->addRegisterCallback(register_all_cocos2dx_extension_manual);

    // chipmunk can be commented out to reduce the package
    sc->addRegisterCallback(jsb_register_chipmunk);
    // opengl can be commented out to reduce the package
    sc->addRegisterCallback(JSB_register_opengl);
    
    // builder can be commented out to reduce the package
    sc->addRegisterCallback(register_all_cocos2dx_builder);
    sc->addRegisterCallback(register_CCBuilderReader);
    
    // ui can be commented out to reduce the package, attension studio need ui module
    sc->addRegisterCallback(register_all_cocos2dx_ui);
    sc->addRegisterCallback(register_all_cocos2dx_ui_manual);

    // studio can be commented out to reduce the package, 
    sc->addRegisterCallback(register_all_cocos2dx_studio);
    sc->addRegisterCallback(register_all_cocos2dx_studio_manual);
    
    // spine can be commented out to reduce the package
    sc->addRegisterCallback(register_all_cocos2dx_spine);
    sc->addRegisterCallback(register_all_cocos2dx_spine_manual);
    
    // XmlHttpRequest can be commented out to reduce the package
    sc->addRegisterCallback(MinXmlHttpRequest::_js_register);
    // websocket can be commented out to reduce the package
    sc->addRegisterCallback(register_jsb_websocket);
    // sokcet io can be commented out to reduce the package
    sc->addRegisterCallback(register_jsb_socketio);
    
    // 3d can be commented out to reduce the package
    sc->addRegisterCallback(register_all_cocos2dx_3d);
    sc->addRegisterCallback(register_all_cocos2dx_3d_manual);
    
    // 3d extension can be commented out to reduce the package
    sc->addRegisterCallback(register_all_cocos2dx_3d_extension);
    
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
    sc->addRegisterCallback(JavascriptJavaBridge::_js_register);
#elif (CC_TARGET_PLATFORM == CC_PLATFORM_IOS|| CC_TARGET_PLATFORM == CC_PLATFORM_MAC)
    sc->addRegisterCallback(JavaScriptObjCBridge::_js_register);
#endif
    
#if (COCOS2D_DEBUG > 0 && CC_CODE_IDE_DEBUG_SUPPORT > 0)
    // NOTE:Please don't remove this call if you want to debug with Cocos Code IDE
    startRuntime();
#else
    // ---- 游戏自研绑定 ----
    // 原本在 Classes/{utilsex,gameshare,xg,talkingdata}/ 下，是游戏私有代码，
    // 这里按从 libcocos2djs.so 挖出的符号表重写。
    sc->addRegisterCallback(register_all_oppai_utilsex);
    sc->addRegisterCallback(register_all_oppai_gameshare);
    sc->addRegisterCallback(register_all_oppai_xg);
    sc->addRegisterCallback(register_all_oppai_talkingdata);
    sc->addRegisterCallback(register_all_oppai_ccs);
    sc->addRegisterCallback(register_all_oppai_csloader);
    sc->addRegisterCallback(register_all_oppai_uihelper);
    sc->addRegisterCallback(register_all_oppai_bugly);
    sc->addRegisterCallback(register_all_oppai_touch);
    sc->addRegisterCallback(register_all_oppai_videoplayer);

    sc->start();
    sc->runScript("script/jsb_boot.js");

    // [oppai] JS-DEBUGGER —— 打开引擎自带的远程 JS 调试器
    //
    // cocos2d-js 3.6 的 ScriptingCore 里本来就有一整套（SpiderMonkey Debugger API
    // + Firefox 远程调试协议 + TCP 服务），但官方模板把这行放在
    // `#if COCOS2D_DEBUG` 里，而我们的 Application.mk 是 NDK_DEBUG=0，
    // 所以从来没被调用过。这里无条件打开。
    //
    // 必须在 runScript("script/jsb_boot.js") **之后**：
    // jsb_debugger.js 的 _prepareDebugger() 第一句就是 `require = global.require;`。
    //
    // 想临时关掉：在可写目录建一个空文件 `jsdebugger.off`
    //   adb shell "run-as com.cm.zcsmw.baidu touch files/jsdebugger.off"   （或直接写文件）
    // 想换端口：在可写目录写 `jsdebugger.port`，内容就是端口号。
    {
        cocos2d::FileUtils* jsdFu = cocos2d::FileUtils::getInstance();
        std::string jsdWritable = jsdFu->getWritablePath();
        if (jsdFu->isFileExist(jsdWritable + "jsdebugger.off")) {
            cocos2d::log("[oppai] JS debugger 已被 jsdebugger.off 关掉");
        } else {
            unsigned int jsdPort = 5086;
            std::string jsdPortFile = jsdWritable + "jsdebugger.port";
            if (jsdFu->isFileExist(jsdPortFile)) {
                int jsdV = atoi(jsdFu->getStringFromFile(jsdPortFile).c_str());
                if (jsdV > 0 && jsdV < 65536) {
                    jsdPort = (unsigned int)jsdV;
                }
            }
            cocos2d::log("[oppai] enableDebugger port=%u  (adb forward tcp:%u tcp:%u)",
                         jsdPort, jsdPort, jsdPort);
            sc->enableDebugger(jsdPort);
        }
    }

    auto engine = ScriptingCore::getInstance();
    ScriptEngineManager::getInstance()->setScriptEngine(engine);
    ScriptingCore::getInstance()->runScript(ConfigParser::getInstance()->getEntryFile().c_str());
#endif
    
    return true;
}

// This function will be called when the app is inactive. When comes a phone call,it's be invoked too
void AppDelegate::applicationDidEnterBackground()
{
    auto director = Director::getInstance();
    director->stopAnimation();
    director->getEventDispatcher()->dispatchCustomEvent("game_on_hide");
    SimpleAudioEngine::getInstance()->pauseBackgroundMusic();
    SimpleAudioEngine::getInstance()->pauseAllEffects();    
}

// this function will be called when the app is active again
void AppDelegate::applicationWillEnterForeground()
{
    auto director = Director::getInstance();
    director->startAnimation();
    director->getEventDispatcher()->dispatchCustomEvent("game_on_show");
    SimpleAudioEngine::getInstance()->resumeBackgroundMusic();
    SimpleAudioEngine::getInstance()->resumeAllEffects();
}
