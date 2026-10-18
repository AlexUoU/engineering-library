#import <Cocoa/Cocoa.h>

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSString *bundle = NSBundle.mainBundle.bundlePath;
        NSString *root = bundle.stringByDeletingLastPathComponent;
        BOOL stop = [NSBundle.mainBundle.bundleIdentifier hasSuffix:@".stop"];
#if defined(__arm64__)
        NSString *architecture = @"-arm64";
#else
        NSString *architecture = @"-x86_64";
#endif
        for (NSString *python in @[@"/opt/homebrew/bin/python3", @"/usr/local/bin/python3", @"/usr/bin/python3"]) {
            if (![NSFileManager.defaultManager isExecutableFileAtPath:python]) continue;
            NSTask *probe = [NSTask new];
            probe.executableURL = [NSURL fileURLWithPath:@"/usr/bin/arch"];
            probe.arguments = @[architecture, python, @"-c", @"import sys; assert sys.version_info >= (3,9)"];
            probe.standardOutput = NSFileHandle.fileHandleWithNullDevice;
            probe.standardError = NSFileHandle.fileHandleWithNullDevice;
            if (![probe launchAndReturnError:nil]) continue;
            [probe waitUntilExit];
            if (probe.terminationStatus != 0) continue;
            NSTask *task = [NSTask new];
            task.executableURL = [NSURL fileURLWithPath:@"/usr/bin/arch"];
            NSMutableArray *args = [NSMutableArray arrayWithArray:@[architecture, python, [root stringByAppendingPathComponent:@"launcher.py"]]];
            if (stop) [args addObject:@"--stop"];
            task.arguments = args;
            task.currentDirectoryURL = [NSURL fileURLWithPath:root];
            if ([task launchAndReturnError:nil]) {
                [task waitUntilExit];
                return task.terminationStatus;
            }
        }
        [NSApplication sharedApplication];
        NSAlert *alert = [NSAlert new];
        alert.messageText = @"需要兼容此 Mac 的 Python 3.9 或更新版本";
        alert.informativeText = @"请从 python.org 安装 macOS universal2 版本后重新打开。无需安装 Rosetta 或翻译模型。";
        [alert addButtonWithTitle:@"知道了"];
        [NSApp activateIgnoringOtherApps:YES];
        [alert runModal];
        return 1;
    }
}
