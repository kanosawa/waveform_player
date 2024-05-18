import tkinter as tk
from tkinter import ttk, Button
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.ticker import MultipleLocator, FuncFormatter
from pydub import AudioSegment
from pydub.playback import _play_with_simpleaudio
import numpy as np
import threading
import time

class AudioApp:
    def __init__(self, master):
        self.master = master
        self.master.title("Audio Player with Interactive Waveform")
        self.master.geometry("800x400")

        # 音声ファイルの読み込み
        self.audio = AudioSegment.from_file("your_audio_file.wav")
        # 音声データをnumpy配列に変換
        self.data = np.array(self.audio.get_array_of_samples())
        self.fs = self.audio.frame_rate

        # 再生位置管理
        self.start_index = 0  # 初期再生開始位置
        self.current_playback_position = 0  # 現在の再生位置
        self.display_window_seconds = 10  # 表示範囲を10秒に設定
        self.is_playing = False  # 再生中かどうかを示すフラグ
        self.play_lock = threading.Lock()  # 再生制御のためのロック

        # 描画用のフィギュアとサブプロットを準備
        self.fig = Figure(figsize=(8, 3), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.plot(self.data)
        self.playhead, = self.ax.plot([], [], 'r', lw=2)  # 再生位置の縦線を初期化
        self.ax.xaxis.set_major_locator(MultipleLocator(self.fs))  # 1秒ごとに目盛りを設定
        self.ax.xaxis.set_major_formatter(FuncFormatter(self.format_seconds))  # 5秒ごとに数値を表示
        self.ax.yaxis.set_visible(False)  # 縦軸の数値表示を削除
        self.fig.subplots_adjust(left=0.05, right=0.95, top=0.95, bottom=0.1)  # パディングを調整
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.master)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=1)

        # スクロールバーの設定
        self.scrollbar = ttk.Scrollbar(self.master, orient=tk.HORIZONTAL, command=self.on_scroll)
        self.scrollbar.pack(fill=tk.X)
        self.update_scrollbar()

        # クリックイベントの設定
        self.canvas.mpl_connect("button_press_event", self.on_press)
        self.canvas.mpl_connect("button_release_event", self.on_release)
        self.canvas.mpl_connect("motion_notify_event", self.on_motion)

        # 再生ボタンと停止ボタンの追加
        self.play_button = Button(self.master, text="Play", command=self.start_playback)
        self.play_button.pack(side=tk.LEFT, padx=10)
        self.stop_button = Button(self.master, text="Stop", command=self.stop_audio_playback)
        self.stop_button.pack(side=tk.LEFT, padx=10)

        # 初期表示の波形設定
        self.update_waveform_display(0)

        # 現在の再生用スレッド
        self.play_thread = None
        self.play_obj = None  # simpleaudioのPlayObject

        # ドラッグアンドドロップの初期化
        self.press = None
        self.highlight_patch = None
        self.drag_threshold = 5  # ドラッグと見なすための移動距離の閾値

        # 選択範囲の初期化
        self.selection_start = None
        self.selection_end = None

    def format_seconds(self, x, pos):
        """X軸のラベルを秒単位でフォーマットする"""
        seconds = x / self.fs
        if seconds % 5 == 0:
            return f'{seconds:.0f} s'
        return ''

    def update_scrollbar(self):
        """スクロールバーの位置を更新する"""
        fraction_start = self.ax.get_xlim()[0] / len(self.data)
        fraction_end = self.ax.get_xlim()[1] / len(self.data)
        self.scrollbar.set(fraction_start, fraction_end)

    def on_scroll(self, *args):
        """スクロールバーの操作イベントを処理する"""
        if args[0] == "moveto":
            fraction = float(args[1])
            new_center = int(fraction * len(self.data))
            self.current_playback_position = new_center
            self.update_waveform_display(new_center / self.fs * 1000)
        elif args[0] == "scroll":
            direction = int(args[1])
            if args[2] == "units":
                step = int(self.display_window_seconds * self.fs / 10)  # 小さい単位でスクロール
            elif args[2] == "pages":
                step = int(self.display_window_seconds * self.fs)
            else:
                return
            current_start, current_end = self.ax.get_xlim()
            window_size = current_end - current_start
            new_start = max(0, current_start + direction * step)
            new_end = new_start + window_size
            if new_end > len(self.data):
                new_end = len(self.data)
                new_start = new_end - window_size
            self.ax.set_xlim(new_start, new_end)
            self.current_playback_position = int(new_start + window_size / 2)  # 中央を再生位置に設定
            self.canvas.draw()
            self.update_scrollbar()
            self.update_waveform_display(self.current_playback_position / self.fs * 1000)  # クリックされた位置を反映して波形を更新

    def on_press(self, event):
        """ドラッグ開始位置を取得"""
        if event.inaxes != self.ax:
            return
        self.press = (event.xdata, event.ydata)
        if self.highlight_patch is not None:
            self.highlight_patch.remove()
            self.highlight_patch = None
        self.selection_start = None
        self.selection_end = None
        self.canvas.draw()

    def on_release(self, event):
        """ドラッグ終了位置を取得し、時間範囲を計算して反転表示"""
        if event.inaxes != self.ax:
            return
        if self.press is None:
            return
        x0, y0 = self.press
        x1, y1 = event.xdata, event.ydata
        self.press = None
        if abs(x1 - x0) < self.drag_threshold and abs(y1 - y0) < self.drag_threshold:
            self.current_playback_position = int(x1)
            self.update_waveform_display(self.current_playback_position / self.fs * 1000)
            return
        start_time = min(x0, x1) / self.fs
        end_time = max(x0, x1) / self.fs
        self.selection_start = min(x0, x1)
        self.selection_end = max(x0, x1)
        if self.highlight_patch is not None:
            self.highlight_patch.remove()
        self.highlight_patch = self.ax.axvspan(self.selection_start, self.selection_end, color='black', alpha=0.5)
        self.canvas.draw()
        print(f"Start Time: {start_time} s, End Time: {end_time} s")

    def on_motion(self, event):
        """ドラッグ中の範囲をハイライト"""
        if self.press is None or event.inaxes != self.ax:
            return
        x0, y0 = self.press
        x1, y1 = event.xdata, event.ydata
        if abs(x1 - x0) < self.drag_threshold and abs(y1 - y0) < self.drag_threshold:
            return
        if self.highlight_patch is not None:
            self.highlight_patch.remove()
        self.highlight_patch = self.ax.axvspan(min(x0, x1), max(x0, x1), color='black', alpha=0.5)
        self.canvas.draw()

    def start_playback(self):
        """再生ボタンが押されたときの処理"""
        with self.play_lock:  # 再生ロックを使用して競合を防止
            if self.is_playing:
                return  # 再生中の場合は無視する

            self.is_playing = True  # 再生中フラグを立てる

            if self.selection_start is not None and self.selection_end is not None:
                # 選択範囲の再生
                self.play_thread = threading.Thread(target=self.play_audio_selection,
                                                    args=(self.selection_start, self.selection_end))
            else:
                # 通常再生
                self.play_thread = threading.Thread(target=self.play_audio, args=(self.current_playback_position,))
            self.play_thread.start()

    def stop_audio_playback(self):
        """現在の音声再生を停止"""
        with self.play_lock:  # 再生ロックを使用して競合を防止
            if self.play_obj:
                self.play_obj.stop()  # simpleaudioの再生を直接停止
                self.play_obj = None
            self.is_playing = False  # 再生中フラグを下げる

    def play_audio(self, start_sample):
        """指定された位置から音声再生"""
        start_ms = int((start_sample / self.fs) * 1000)
        segment = self.audio[start_ms:]
        self.play_obj = _play_with_simpleaudio(segment)

        # 再生中の波形表示を更新
        start_time = time.time()
        while self.play_obj and self.play_obj.is_playing():
            elapsed_time = time.time() - start_time
            current_position_ms = start_ms + int(elapsed_time * 1000)
            self.current_playback_position = int(current_position_ms / 1000 * self.fs)
            self.update_waveform_display(current_position_ms)
            time.sleep(0.05)  # UIがフリーズしないように少し待機

        self.play_obj = None  # 再生が終了または中断されたらplay_objをNoneに設定
        with self.play_lock:
            self.is_playing = False  # 再生中フラグを下げる

    def play_audio_selection(self, start_sample, end_sample):
        """指定された範囲の音声を再生"""
        start_ms = int((start_sample / self.fs) * 1000)
        end_ms = int((end_sample / self.fs) * 1000)
        segment = self.audio[start_ms:end_ms]
        self.play_obj = _play_with_simpleaudio(segment)

        # 再生中の波形表示を更新
        start_time = time.time()
        while self.play_obj and self.play_obj.is_playing():
            elapsed_time = time.time() - start_time
            current_position_ms = start_ms + int(elapsed_time * 1000)
            if current_position_ms >= end_ms:
                self.play_obj.stop()
                break
            self.current_playback_position = int(current_position_ms / 1000 * self.fs)
            self.update_waveform_display(current_position_ms)

        self.play_obj = None  # 再生が終了または中断されたらplay_objをNoneに設定
        with self.play_lock:
            self.is_playing = False  # 再生中フラグを下げる

    def update_waveform_display(self, position_ms):
        """再生位置を中央にして波形と縦線を更新"""
        center_sample_index = int(position_ms / 1000 * self.fs)
        window_samples = int(self.display_window_seconds * self.fs / 2)
        start_index = max(0, center_sample_index - window_samples)
        end_index = start_index + 2 * window_samples
        if end_index > len(self.data):
            end_index = len(self.data)
            start_index = end_index - 2 * window_samples

        # Y軸の範囲を取得
        y_min, y_max = self.ax.get_ylim()

        self.ax.set_xlim(start_index, end_index)
        self.playhead.set_data([center_sample_index, center_sample_index], [y_min, y_max])
        self.canvas.draw()
        self.update_scrollbar()


def main():
    root = tk.Tk()
    app = AudioApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
